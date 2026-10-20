# -*- coding: utf-8 -*-
"""Comparer deux cartes, et n'avoir qu'une seule façon de le faire.

Les identifiants de niveau et de nœud sont conservés d'une carte dérivée à
l'autre : c'est ce qui permet de dire « cette étape a été renommée » plutôt que
« une étape a disparu, une autre est apparue ». Sans eux, un diff n'aurait rien
à dire d'utile.

Le calcul sert deux appelants qui n'ont pas le même horizon :

* l'assistant de comparaison, qui affiche un rapport et l'oublie ;
* le semis des écarts vers un processus souhaité, qui les garde, les fait
  porter par quelqu'un et les suit jusqu'au bout.

D'où une seule fonction de calcul, `calculer_ecarts`, et deux rendus. Deux
implémentations auraient divergé au premier genre d'écart ajouté d'un côté.
Chaque écart porte une **clé stable** : c'est elle qui permet à un second semis
de reconnaître un écart déjà consigné au lieu d'en créer un jumeau et d'effacer
ce qu'une personne avait écrit dessus.
"""
from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import UserError

#: Les portées, dans l'ordre où elles se lisent, avec leur titre de bloc.
PORTEES = (
    ("niveau", "Levels"),
    ("noeud", "Nodes"),
    ("flux", "Flows"),
    ("message", "Message flow"),
)

#: Ce qu'un écart fait à l'étape qu'il vise. Sert aux teintes : un retrait se
#: montre sur la carte d'avant, un ajout sur celle d'après, une modification
#: sur les deux.
COTES = {
    "ajout": ("apres",),
    "retrait": ("avant",),
    "renommage": ("avant", "apres"),
    "nature": ("avant", "apres"),
    "couloir": ("avant", "apres"),
    "ton": ("avant", "apres"),
}


def _index(processus):
    """Aplati une carte en dictionnaires indexés par code."""
    niveaux, noeuds, flux, msgs = {}, {}, {}, {}
    for d in processus.diagram_ids:
        niveaux[d.code] = d
        for n in d.node_ids:
            noeuds[(d.code, n.code)] = n
        for f in d.flow_ids:
            flux[(d.code, f.source_id.code, f.target_id.code)] = f
        for m in d.message_ids:
            msgs[(d.code, m.node_id.code, m.pool_id.code)] = m
    return niveaux, noeuds, flux, msgs


def _ecart(portee, genre, cle, libelle, diagram_code="", diagram_titre="",
           node_code=""):
    return {
        "portee": portee,
        "genre": genre,
        "cle": cle,
        "libelle": libelle,
        "diagram_code": diagram_code,
        "diagram_titre": diagram_titre,
        "node_code": node_code,
    }


def calculer_ecarts(avant, apres):
    """Ce qui sépare deux cartes, dans l'ordre où ça se lit.

    `avant` est la carte de référence (la version précédente, ou l'état
    actuel), `apres` celle qu'on lui compare (la version récente, ou le
    processus souhaité). Rend une liste de dictionnaires plats : le rapport
    HTML les groupe, le semis les enregistre.

    Un changement se dit « A vers B », jamais avec une flèche : le plan de
    transformation s'imprime en Lexend, qui n'a pas de glyphe pour « → »
    (U+2192), et reportlab pose alors un carré blanc à sa place.

    Les libellés se composent dans la langue de l'environnement d'`apres` :
    l'assistant y passe celle de la session, le semis celle du livrable (le
    libellé est stocké, et il s'imprime au plan de transformation du client).
    Genres et tons sont les libellés des sélections, jamais une table à part.
    """
    env = apres.env
    champs = env["bf.process.node"]._fields
    genres = dict(champs["kind"]._description_selection(env))
    tons = dict(champs["tone"]._description_selection(env))
    aucun = env._("none")
    sans_porte = env._("no gate")

    def genre(kind):
        return genres.get(kind, kind)

    def ton(tone):
        return tons[tone].lower() if tone in tons else (tone or aucun)

    av_n, av_no, av_f, av_m = _index(avant)
    ap_n, ap_no, ap_f, ap_m = _index(apres)
    out = []

    for code in sorted(set(ap_n) - set(av_n)):
        d = ap_n[code]
        out.append(_ecart("niveau", "ajout", f"niveau:ajout:{code}",
                          env._("level \"%(titre)s\"", titre=d.title),
                          code, d.title))
    for code in sorted(set(av_n) - set(ap_n)):
        d = av_n[code]
        out.append(_ecart("niveau", "retrait", f"niveau:retrait:{code}",
                          env._("level \"%(titre)s\"", titre=d.title),
                          code, d.title))
    for code in sorted(set(av_n) & set(ap_n)):
        if av_n[code].title != ap_n[code].title:
            out.append(_ecart(
                "niveau", "renommage", f"niveau:renommage:{code}",
                env._("\"%(avant)s\" to \"%(apres)s\"",
                      avant=av_n[code].title, apres=ap_n[code].title),
                code, ap_n[code].title))

    for cle in sorted(set(ap_no) - set(av_no)):
        n = ap_no[cle]
        out.append(_ecart(
            "noeud", "ajout", f"noeud:ajout:{cle[0]}:{cle[1]}",
            env._("%(genre)s \"%(nom)s\" in \"%(niveau)s\"",
                  genre=genre(n.kind), nom=n.name or n.code,
                  niveau=n.diagram_id.title),
            cle[0], n.diagram_id.title, cle[1]))
    for cle in sorted(set(av_no) - set(ap_no)):
        n = av_no[cle]
        out.append(_ecart(
            "noeud", "retrait", f"noeud:retrait:{cle[0]}:{cle[1]}",
            env._("%(genre)s \"%(nom)s\" in \"%(niveau)s\"",
                  genre=genre(n.kind), nom=n.name or n.code,
                  niveau=n.diagram_id.title),
            cle[0], n.diagram_id.title, cle[1]))
    for cle in sorted(set(av_no) & set(ap_no)):
        a, b = av_no[cle], ap_no[cle]
        ou = b.diagram_id.title
        commun = (cle[0], b.diagram_id.title, cle[1])
        if (a.name or "") != (b.name or ""):
            out.append(_ecart(
                "noeud", "renommage", f"noeud:renommage:{cle[0]}:{cle[1]}",
                env._("\"%(avant)s\" to \"%(apres)s\" in \"%(niveau)s\"",
                      avant=a.name or a.code, apres=b.name or b.code,
                      niveau=ou), *commun))
        if a.kind != b.kind:
            out.append(_ecart(
                "noeud", "nature", f"noeud:nature:{cle[0]}:{cle[1]}",
                env._("\"%(nom)s\": %(avant)s to %(apres)s in \"%(niveau)s\"",
                      nom=b.name or b.code, avant=genre(a.kind).lower(),
                      apres=genre(b.kind).lower(), niveau=ou), *commun))
        if (a.lane_id.code or "") != (b.lane_id.code or ""):
            out.append(_ecart(
                "noeud", "couloir", f"noeud:couloir:{cle[0]}:{cle[1]}",
                env._("\"%(nom)s\": %(avant)s to %(apres)s in \"%(niveau)s\"",
                      nom=b.name or b.code, avant=a.lane_id.name or aucun,
                      apres=b.lane_id.name or aucun, niveau=ou), *commun))
        if (a.tone or "") != (b.tone or ""):
            out.append(_ecart(
                "noeud", "ton", f"noeud:ton:{cle[0]}:{cle[1]}",
                env._("annotation \"%(nom)s…\": %(avant)s to %(apres)s in "
                      "\"%(niveau)s\"",
                      nom=(b.name or "")[:40], avant=ton(a.tone),
                      apres=ton(b.tone), niveau=ou), *commun))

    def bout(lien):
        return lien.name or lien.code

    for cle in sorted(set(ap_f) - set(av_f), key=str):
        f = ap_f[cle]
        out.append(_ecart(
            "flux", "ajout", f"flux:ajout:{cle[0]}:{cle[1]}>{cle[2]}",
            env._("%(depart)s to %(cible)s", depart=bout(f.source_id),
                  cible=bout(f.target_id)),
            cle[0], f.diagram_id.title))
    for cle in sorted(set(av_f) - set(ap_f), key=str):
        f = av_f[cle]
        out.append(_ecart(
            "flux", "retrait", f"flux:retrait:{cle[0]}:{cle[1]}>{cle[2]}",
            env._("%(depart)s to %(cible)s", depart=bout(f.source_id),
                  cible=bout(f.target_id)),
            cle[0], f.diagram_id.title))
    for cle in sorted(set(av_f) & set(ap_f), key=str):
        a, b = av_f[cle], ap_f[cle]
        if (a.label or "") != (b.label or ""):
            out.append(_ecart(
                "flux", "porte", f"flux:porte:{cle[0]}:{cle[1]}>{cle[2]}",
                env._("%(depart)s to %(cible)s: \"%(avant)s\" to \"%(apres)s\"",
                      depart=bout(b.source_id), cible=bout(b.target_id),
                      avant=a.label or sans_porte, apres=b.label or sans_porte),
                cle[0], b.diagram_id.title))

    for cle in sorted(set(ap_m) - set(av_m), key=str):
        m = ap_m[cle]
        out.append(_ecart(
            "message", "ajout", f"message:ajout:{cle[0]}:{cle[1]}:{cle[2]}",
            env._("\"%(libelle)s\"", libelle=m.label), cle[0],
            m.diagram_id.title))
    for cle in sorted(set(av_m) - set(ap_m), key=str):
        m = av_m[cle]
        out.append(_ecart(
            "message", "retrait", f"message:retrait:{cle[0]}:{cle[1]}:{cle[2]}",
            env._("\"%(libelle)s\"", libelle=m.label), cle[0],
            m.diagram_id.title))
    for cle in sorted(set(av_m) & set(ap_m), key=str):
        a, b = av_m[cle], ap_m[cle]
        if a.label != b.label:
            out.append(_ecart(
                "message", "libelle",
                f"message:libelle:{cle[0]}:{cle[1]}:{cle[2]}",
                env._("\"%(avant)s\" to \"%(apres)s\"", avant=a.label,
                      apres=b.label),
                cle[0], b.diagram_id.title))
    return out


TEINTES_RAPPORT = {"ajout": "#1B8A4B", "retrait": "#C0392B"}


def rendre_rapport(ecarts, env, entete=Markup("")):
    """Compose le rapport en échappant tout ce qui vient d'un enregistrement.

    Les libellés comparés sont saisis par l'utilisateur, importés d'un `.bpmn`
    tiers ou posés depuis le tracé : ils ne sont pas sûrs. Chaque valeur passe
    donc par `Markup %`, qui échappe ses arguments, et le champ garde son
    `sanitize`. Concaténer ces textes en f-string dans du HTML rendu tel quel
    ferait exécuter un nom de nœud dans la session de quiconque ouvre la
    comparaison.

    `env` donne la langue du rapport (celle de la session qui compare) : les
    titres de bloc et les genres sont les libellés des sélections de l'écart,
    jamais la clé technique.
    """
    if not ecarts:
        return entete + Markup("<p>%s</p>") % env._(
            "No gap: both maps say the same thing.")
    champs = env["bf.process.ecart"]._fields
    portees = dict(champs["portee"]._description_selection(env))
    genres = dict(champs["genre"]._description_selection(env))
    blocs = []
    for portee, _titre in PORTEES:
        titre = portees.get(portee, portee)
        lignes = [e for e in ecarts if e["portee"] == portee]
        if not lignes:
            continue
        # le deux-points se traduit avec sa phrase : une fine insécable en
        # français, rien en anglais
        ligne = Markup(env._("%(genre)s: %(libelle)s"))
        items = Markup("").join(
            Markup("<li>%s</li>") % (ligne % {
                "genre": Markup("<span style='color:%s;font-weight:600'>%s"
                                "</span>") % (
                    TEINTES_RAPPORT.get(e["genre"], "#B8860B"),
                    genres.get(e["genre"], e["genre"])),
                "libelle": e["libelle"]})
            for e in lignes)
        blocs.append(Markup("<h4>%s <span class='text-muted'>(%s)</span>"
                            "</h4><ul>%s</ul>") % (titre, len(lignes), items))
    return entete + Markup("").join(blocs)


class BfProcessCompareWizard(models.TransientModel):
    _name = "bf.process.compare.wizard"
    _description = "Compare two maps"

    source_id = fields.Many2one(
        "bf.process", string="Recent version", required=True)
    cible_id = fields.Many2one(
        "bf.process", string="Compare with", required=True,
        help="Usually the previous version, or the current state a target "
             "process is drawn from.")
    # `sanitize` reste actif : c'est la deuxième couche. La première est
    # l'échappement de chaque valeur interpolée dans `rendre_rapport`.
    rapport_html = fields.Html(string="Gaps", readonly=True)
    ecart_count = fields.Integer(string="Number of gaps", readonly=True)

    @api.onchange("source_id")
    def _onchange_source(self):
        for rec in self:
            if rec.source_id and not rec.cible_id:
                rec.cible_id = (rec.source_id.origine_id
                                or rec.source_id.version_precedente_id)

    def action_comparer(self):
        self.ensure_one()
        if self.source_id == self.cible_id:
            raise UserError(_("Comparing a map with itself says nothing."))
        ecarts = calculer_ecarts(self.cible_id, self.source_id)
        self.ecart_count = len(ecarts)
        self.rapport_html = rendre_rapport(ecarts, self.env, self._entete())
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }

    def _entete(self):
        self.ensure_one()
        return Markup("<p class='text-muted'>%s</p>") % (Markup(_(
            "From <b>%s</b> to <b>%s</b>.")) % (self.cible_id.display_name,
                                                self.source_id.display_name))
