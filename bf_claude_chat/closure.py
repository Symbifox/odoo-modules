"""Chaque conversation Gen vise sa fermeture, comme un billet.

En fin de tour, Gen juge lui-même où en est la conversation et le dit par une
balise cachée, la dernière chose de sa réponse :

    <closure state="done">livré et vérifié en production</closure>

La balise ne doit JAMAIS atteindre un écran ni la base. Elle passe par deux
chemins : le flux relayé au bureau (événements `text`, puis `done` qui rend la
réponse entière) et le contenu écrit au fil de l'eau, que le téléphone lit.
`ClosureFilter` retient la fin du flux dès qu'elle pourrait commencer une
balise, `strip_closure` nettoie un texte complet.

La consigne part AU MESSAGE du tour, pas dans l'invite système : une section
de l'invite n'est pas suivie, une note au message l'est (mesuré le 2026-09-14).
Elle est écrite en français : le pont devine la langue de ses messages de
repli sur le message (`_detect_lang`), et un seul « the » la ferait
passer à l'anglais pour tout le monde.
"""

import re

#: États qu'une balise peut porter.
STATES = ("open", "waiting", "ideation", "done")

_OPEN = "<closure"
# Une balise complète, ancrée à sa position. La raison ne contient pas de
# chevron : une balise qui en porterait un n'est pas la nôtre.
_TAG = re.compile(r'<closure((?:\s+[a-z_]+="[^"<>]*")*)\s*>([^<]{0,400})</closure>',
                  re.I)
_ATTR = re.compile(r'([a-z_]+)="([^"<>]*)"', re.I)
# Au-delà, un « <closure » resté ouvert n'est pas une balise en cours
# d'écriture : on rend le texte à l'écran.
_MAX_TAG = 600


# Le début d'une vraie balise, restée sans fin quand le tour s'arrête :
# « <closure » suivi d'un blanc, ou rien après.
_CUT_TAG = re.compile(r'<closure(?:\s|$)', re.I)


def _may_become_tag(pending):
    """Ce texte, qui commence par « <closure », peut-il encore en être une ?

    Une balise suit « <closure » d'un blanc ou d'un chevron : « `<closure` »
    dans du code est rendu sans attendre. Elle peut s'étendre sur plusieurs
    lignes, mais pas au-delà de _MAX_TAG caractères.
    """
    if len(pending) >= _MAX_TAG:
        return False
    return len(pending) == len(_OPEN) or pending[len(_OPEN)] in " \t\r\n>"


def _verdict(match):
    attrs = {k.lower(): v for k, v in _ATTR.findall(match.group(1) or "")}
    state = (attrs.get("state") or "").strip().lower()
    if state not in STATES:
        return None
    task = attrs.get("task") or ""
    return {
        "state": state,
        "reason": " ".join((match.group(2) or "").split())[:300],
        "task_id": int(task) if task.isdigit() and len(task) <= 9 else 0,
    }


def _partial_suffix(text):
    """Longueur de la fin de `text` qui pourrait commencer « <closure »."""
    for n in range(min(len(_OPEN) - 1, len(text)), 0, -1):
        if _OPEN.startswith(text[-n:].lower()):
            return n
    return 0


class ClosureFilter:
    """Retire les balises d'un flux de texte, morceau par morceau.

    `feed` rend la part affichable du morceau ; ce qui pourrait commencer une
    balise attend le morceau suivant. `finish` rend le reste à la fin du tour.
    Le dernier verdict lisible est dans `verdict`.
    """

    def __init__(self):
        self.pending = ""
        self.verdict = None

    def feed(self, delta):
        self.pending += delta or ""
        return self._release(final=False)

    def finish(self):
        return self._release(final=True)

    def _release(self, final):
        out = []
        while self.pending:
            i = self.pending.lower().find(_OPEN)
            if i < 0:
                garde = 0 if final else _partial_suffix(self.pending)
                out.append(self.pending[:len(self.pending) - garde])
                self.pending = self.pending[len(self.pending) - garde:]
                break
            out.append(self.pending[:i])
            self.pending = self.pending[i:]
            match = _TAG.match(self.pending)
            if match:
                verdict = _verdict(match)
                if verdict:
                    self.verdict = verdict
                self.pending = self.pending[match.end():]
                continue
            if final and _CUT_TAG.match(self.pending):
                # Une balise coupée par la fin du tour : elle ne s'affiche pas.
                self.pending = ""
                break
            if not final and _may_become_tag(self.pending):
                break  # la balise s'écrit encore
            # Un « <closure » qui ne deviendra pas une balise (du code, une
            # citation) : c'est du texte, rendu tout de suite.
            out.append(self.pending[0])
            self.pending = self.pending[1:]
        return "".join(out)


def strip_closure(text):
    """(texte sans balise, dernier verdict ou None) pour un texte complet."""
    filtre = ClosureFilter()
    propre = filtre.feed(text or "") + filtre.finish()
    return propre, filtre.verdict


def closure_note(unlinked=False):
    """La consigne ajoutée au message du tour.

    Le jugement porte sur le travail de la CONVERSATION, plus sur le but de
    la personne : quand la suite est déposée ailleurs (brouillon au chatter,
    activité, échéance), la personne ferme la conversation, et l'ancienne
    règle la gardait ouverte. Une question ou une offre en fin de réponse
    reste « t'attend » : la personne y répond d'habitude par un « go ». Un
    brouillon montré ici seulement est « t'attend » s'il se valide ici.

    `unlinked` : la conversation n'a pas de fiche et Gen n'a encore rien
    proposé ; il peut nommer une tâche qu'il connaît, jamais en chercher une
    exprès (un tour ne doit rien coûter de plus pour ça).
    """
    note = (
        "\n\n<fermeture>Note de l'écran, pas de la personne : ne la mentionne "
        "jamais. Termine ta réponse par une seule balise, seule sur sa dernière "
        "ligne ; l'écran la retire avant de l'afficher :\n"
        '<closure state="ÉTAT">raison en une ligne</closure>\n'
        "ÉTAT juge le travail de cette conversation, pas tout le dossier. "
        "Regarde d'abord comment finit ta réponse. Si elle se termine par une "
        "question, un choix à faire, des options ou une offre d'agir toi-même "
        "(« je peux… », « veux-tu que… », « dis-moi si… », « j'attends ton feu "
        "vert »), c'est waiting : la personne répond d'habitude ici. Sinon, done "
        "quand ce que la conversation devait faire est fait et que ce qui reste "
        "vit ailleurs qu'ici : déposé dans Odoo (brouillon au chatter, activité, "
        "échéance, rappel, note) ou un geste que la personne fera seule (envoyer "
        "un brouillon déjà parqué, attendre la réponse d'un tiers, saisir son "
        "temps). Un brouillon montré seulement ici, du code écrit mais pas encore "
        "intégré, déployé et vérifié, un plan, un banc ou une maquette, ce n'est "
        "pas done : waiting si la personne doit le valider ici, open s'il "
        "reste du travail pour toi. ideation si l'on "
        "explore une idée et que rien n'est encore décidé ni lancé. La raison dit "
        "en quelques mots ce qui a été livré et où vit la suite, dans la langue "
        "de la personne."
    )
    if unlinked:
        note += (
            " Cette conversation n'est rattachée à aucune fiche. Si elle porte "
            "sur une tâche Odoo précise dont le numéro a été cité ou lu dans "
            'cette conversation, ajoute task="NUMÉRO" dans la balise, par '
            'exemple <closure state="open" task="1234">…</closure>. Sinon '
            "n'ajoute rien, et ne cherche pas de tâche exprès pour ça."
        )
    return note + "</fermeture>"
