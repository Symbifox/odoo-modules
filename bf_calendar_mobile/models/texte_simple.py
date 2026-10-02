"""Texte d'un téléphone ↔ HTML d'Odoo, sans rien perdre.

Un champ texte au téléphone ne sait reproduire que des paragraphes. Une
description écrite au bureau peut porter une liste, du gras, un lien, une
image : la réécrire depuis un champ texte l'aplatirait en silence. Elle est
donc lue (approximativement) mais pas réécrite.

Repris de ``bf_bloc_notes`` (``bf_note_mobile.py``, même règle, mêmes pièges)
plutôt qu'importé : ce module ne dépend pas du bloc-notes, et la règle tient en
quelques lignes.
"""

from lxml import html as lxml_html
from markupsafe import Markup

from odoo.tools import html2plaintext

#: Balises qu'un champ texte sait reproduire. Tout le reste rend le HTML riche.
PLAIN_TAGS = {"p", "div", "br", "span"}
BLOCK_TAGS = {"p", "div"}

#: Attributs qui ne portent aucune mise en forme. 🔴 `data-oe-version` est posé
#: par l'éditeur d'Odoo sur l'enveloppe de tout texte écrit au bureau : sans lui
#: ici, une description toute simple serait rendue « riche » sans raison.
#: `class` n'est admis que VIDE : les colonnes de l'éditeur (`o_text_columns`,
#: `row`, `col-6`), un encadré (`alert`) ou un alignement sont des classes sur
#: des `<div>` et des `<p>`, et les réécrire depuis un champ texte les
#: aplatirait (relecture adverse). Mesuré le 2026-10-02 : aucune des 287
#: descriptions des tâches ouvertes d'une personne sur une base réelle ne
#: change de camp avec cette règle.
HARMLESS_ATTRS = {"class", "data-oe-version"}


def texte_vers_html(texte):
    """Une ligne = un paragraphe ; une ligne vide garde sa place."""
    lignes = (texte or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    # Retirer les lignes vides de la FIN seulement : celles du milieu sont
    # l'espacement voulu par la personne.
    while lignes and not lignes[-1].strip():
        lignes.pop()
    if not lignes:
        return ""
    return Markup("").join(
        Markup("<p>%s</p>") % ligne if ligne.strip() else Markup("<p><br></p>")
        for ligne in lignes
    )


def html_est_simple(html):
    """Vrai si le HTML ne porte que des paragraphes de texte.

    Le test est structurel (balises et attributs), pas un aller-retour de
    chaînes : Odoo réécrit le HTML à l'assainissement, et une comparaison
    textuelle rendrait « riche » un texte qui ne l'est pas.
    """
    if not html or not str(html).strip():
        return True
    try:
        fragments = lxml_html.fragments_fromstring(str(html))
    except Exception:  # noqa: BLE001 — illisible = on ne le réécrit pas
        return False
    for fragment in fragments:
        if isinstance(fragment, str):
            continue
        for element in fragment.iter():
            if not isinstance(element.tag, str):
                # Commentaire HTML : la réécriture le perdrait.
                return False
            if element.tag not in PLAIN_TAGS:
                return False
            if element.tag == "span" and element.attrib:
                return False
            if element.tag != "span" and set(element.attrib) - HARMLESS_ATTRS:
                return False
            if (element.get("class") or "").strip():
                return False
    return True


def html_vers_texte(html):
    """Ligne par ligne pour un HTML simple, approximatif (``html2plaintext``)
    pour un HTML riche, qui n'est alors que lu."""
    if not html or not str(html).strip():
        return ""
    if not html_est_simple(html):
        return html2plaintext(str(html)).strip()
    lignes = []

    def en_ligne(element):
        # `<br>` à l'intérieur d'un paragraphe = saut de ligne voulu.
        morceaux = [element.text or ""]
        for enfant in element:
            morceaux.append("\n" if enfant.tag == "br" else enfant.text_content())
            morceaux.append(enfant.tail or "")
        texte = "".join(morceaux)
        # Un paragraphe qui ne contient qu'un `<br>` est une ligne vide.
        return "" if not texte.strip() else texte.rstrip("\n")

    def parcourir(noeuds, texte_de_tete=None):
        # 🔴 Un bloc peut en contenir d'autres : l'éditeur enveloppe le texte
        # dans un `<div data-oe-version>` qui porte les `<p>`. Aplatir
        # l'enveloppe d'un coup collerait tous les paragraphes en une ligne.
        if texte_de_tete and texte_de_tete.strip():
            lignes.append(texte_de_tete.strip())
        for noeud in noeuds:
            if isinstance(noeud, str):
                if noeud.strip():
                    lignes.append(noeud.strip())
                continue
            if noeud.tag in BLOCK_TAGS and any(
                    isinstance(enfant.tag, str) and enfant.tag in BLOCK_TAGS
                    for enfant in noeud):
                parcourir(list(noeud), noeud.text)
            elif noeud.tag in BLOCK_TAGS:
                lignes.append(en_ligne(noeud))
            elif noeud.tag == "br":
                lignes.append("")
            else:
                lignes.append(noeud.text_content())
            if noeud.getparent() is not None and noeud.tail and noeud.tail.strip():
                lignes.append(noeud.tail.strip())

    parcourir(lxml_html.fragments_fromstring(str(html)))
    # Les lignes vides de FIN ne sont pas gardées à l'écriture : les garder à
    # la lecture rendrait un aller-retour inexact.
    while lignes and not lignes[-1].strip():
        lignes.pop()
    return "\n".join(lignes)
