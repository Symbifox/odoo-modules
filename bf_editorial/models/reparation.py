# -*- coding: utf-8 -*-
"""Les réparations mécaniques : ce qui se corrige sans jugement.

Le module a tenu une promesse simple depuis son premier commit — il lit, il
mesure, il refuse, il ne réécrit pas. Elle a tenu jusqu'à ce qu'on mesure ce
qu'elle coûtait : sur 165 billets, 63 portent des titres vides laissés par
l'éditeur de site, et personne ne va corriger dix balises à la main, dans deux
langues, avant chaque publication. Une garde qui refuse pour un défaut que la
machine sait réparer seule n'est pas une garde, c'est une corvée.

La frontière est donc déplacée, pas effacée. Deux corrections, et seulement
deux, toutes les deux sans arbitrage possible :

* un titre vide (``<h2><br></h2>``) redevient l'espacement qu'il aurait dû
  être (``<p><br></p>``). Un titre sans texte est un échec WCAG 2.4.6 quoi
  qu'il contienne, et l'éditeur de site en produit à chaque passage ;
* un en-tête de tableau sans portée reçoit la sienne, déduite de la FORME de
  la ligne et jamais du sens des mots.

Ce qui demande un jugement reste dehors et le restera : les tirets cadratins
(le remplacement dépend de la phrase), les formules bannies (il faut
réécrire), les marqueurs de rédaction (il manque un visuel), les textes
alternatifs (il faut regarder l'image). Ceux-là restent la charge d'un humain,
ou d'une proposition GenFox qu'un humain applique.

⚠️ Aucune fonction de ce fichier ne touche à la base. Elles prennent du HTML
et rendent du HTML : c'est ce qui les rend testables sans locataire, et c'est
ce qui garde la décision d'écrire au seul endroit qui la porte.
"""

import re

# Un titre dont le contenu se réduit à du vide. La référence arrière ``\1``
# ferme sur le MÊME niveau : ``<h2>…</h3>`` n'est pas un titre vide, c'est du
# HTML cassé, et le réparer en silence masquerait le vrai problème.
_TITRE_VIDE_RE = re.compile(
    r"<h([1-6])\b[^>]*>(?:\s|&nbsp;|<br\s*/?>)*</h\1\s*>", re.I
)

# ⚠️ La limite de mot après « th » est impérative, ici comme dans la QA :
# « th » est un préfixe littéral de « thead ».
_TH_SANS_PORTEE_RE = re.compile(r"<th\b(?![^>]*\bscope\s*=)([^>]*?)(/?)>", re.I)

_LIGNE_RE = re.compile(r"<tr\b[^>]*>.*?</tr\s*>", re.I | re.S)
_CELLULE_RE = re.compile(r"<(th|td)\b", re.I)

ESPACEMENT = "<p><br></p>"


def portee_de_la_ligne(ligne):
    """La portée que la forme de la ligne impose, ou rien si elle n'impose pas.

    Deux formes seulement se lisent sans interpréter le texte :

    * une ligne faite uniquement d'en-têtes est une ligne de titres de
      colonnes, donc ``col`` ;
    * une ligne dont le PREMIER et le SEUL en-tête précède des cellules
      ordinaires est un titre de ligne, donc ``row``.

    Tout le reste (deux en-têtes puis des données, un en-tête au milieu, une
    ligne sans en-tête) rend ``None`` et n'est pas touché. Deviner y serait
    un jugement, et le jugement n'est pas de ce fichier.
    """
    cellules = [m.group(1).lower() for m in _CELLULE_RE.finditer(ligne)]
    if "th" not in cellules:
        return None
    if all(cellule == "th" for cellule in cellules):
        return "col"
    if cellules[0] == "th" and all(c == "td" for c in cellules[1:]):
        return "row"
    return None


def corriger(html):
    """Rendre le HTML réparé et le compte de ce qui a bougé.

    Le rapport est rendu même quand rien ne bouge : c'est lui qui permet à
    l'appelant de n'écrire en base que ce qui a changé.
    """
    rapport = {"titres_vides": 0, "portees": 0}
    if not html:
        return html, rapport

    def _remplacer_titre(match):
        rapport["titres_vides"] += 1
        return ESPACEMENT

    resultat = _TITRE_VIDE_RE.sub(_remplacer_titre, html)

    def _traiter_ligne(match):
        ligne = match.group(0)
        portee = portee_de_la_ligne(ligne)
        if not portee:
            return ligne

        def _poser_portee(cellule):
            rapport["portees"] += 1
            attributs, fermeture = cellule.group(1), cellule.group(2)
            return '<th scope="%s"%s%s>' % (portee, attributs, fermeture)

        return _TH_SANS_PORTEE_RE.sub(_poser_portee, ligne)

    resultat = _LIGNE_RE.sub(_traiter_ligne, resultat)
    return resultat, rapport


def rapport_lisible(rapport, _traduire=lambda texte, *args: texte % args):
    """Une phrase française à partir du compte, ou rien s'il n'y a rien."""
    morceaux = []
    if rapport.get("titres_vides"):
        morceaux.append(_traduire(
            "%s titre(s) vide(s) remplacé(s) par un espacement",
            rapport["titres_vides"],
        ))
    if rapport.get("portees"):
        morceaux.append(_traduire(
            "%s portée(s) posée(s) sur des en-têtes de tableau",
            rapport["portees"],
        ))
    return ", ".join(morceaux)
