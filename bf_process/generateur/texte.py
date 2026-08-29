# -*- coding: utf-8 -*-
"""Le HTML d'une section, converti en objets de mise en page reportlab.

Le moteur de référence, hors serveur, pose du HTML directement dans une boîte
(`insert_htmlbox` de PyMuPDF). Le module n'a pas cette bibliothèque, et n'a
aucune raison de l'ajouter : son manifeste revendique un tracé « sans
navigateur ni moteur typographique », et reportlab sait mettre en page, à
condition qu'on lui donne des objets plutôt que des balises.

Le sous-ensemble converti est celui qu'un champ `Html` sanitisé d'Odoo produit
en pratique : `h3`, `h4`, `p`, `ul`, `ol`, `li`, `blockquote`, et en ligne
`b`, `strong`, `i`, `em`, `u`, `br`, `code`, `a`. `Paragraph` de reportlab
accepte déjà les balises en ligne — ce sont les balises de BLOC qu'il faut
traduire, et c'est tout ce que ce module fait.

⚠️ Une balise inconnue n'est pas ignorée : son contenu est conservé. Perdre
silencieusement une phrase parce qu'elle était dans un `<div>` serait le pire
des comportements pour un livrable.
"""
import html as _html
import re
from html.parser import HTMLParser

BLOCS = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote", "div"}
LISTES = {"ul", "ol"}
EN_LIGNE = {"b", "strong", "i", "em", "u", "br", "code", "a", "span", "sub", "sup"}
# ce que `Paragraph` sait lire tel quel ; le reste de la balise est retiré,
# mais jamais son texte
GARDEES = {"b", "i", "u", "br", "sub", "super"}
EQUIVALENTS = {"strong": "b", "em": "i", "code": "i", "sup": "super"}


class _Lecture(HTMLParser):
    """Rend une liste de (genre, texte en ligne), à plat.

    `genre` vaut `h3`, `h4`, `p`, `puce` ou `numero`. Le texte en ligne ne
    porte que les balises que `Paragraph` accepte.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocs = []
        self._pile = []          # les balises de bloc ouvertes
        self._listes = []        # ('ul'|'ol', compteur)
        self._tampon = []

    # -- accumulation
    def _pousser(self, genre):
        texte = "".join(self._tampon).strip()
        texte = re.sub(r"\s+", " ", texte)
        self._tampon = []
        if texte:
            self.blocs.append((genre, texte))

    def _genre_courant(self):
        for balise in reversed(self._pile):
            if balise == "li":
                if self._listes and self._listes[-1][0] == "ol":
                    return "numero"
                return "puce"
            if balise in ("h1", "h2", "h3"):
                return "h3"
            if balise in ("h4", "h5", "h6"):
                return "h4"
            if balise == "blockquote":
                return "cite"
            if balise in ("p", "div"):
                return "p"
        return "p"

    # -- analyse
    def handle_starttag(self, tag, attrs):
        if tag in LISTES:
            self._pousser(self._genre_courant())
            self._listes.append([tag, 0])
        elif tag in BLOCS:
            self._pousser(self._genre_courant())
            self._pile.append(tag)
            if tag == "li" and self._listes:
                self._listes[-1][1] += 1
        elif tag == "br":
            self._tampon.append("<br/>")
        elif tag in EN_LIGNE:
            garde = EQUIVALENTS.get(tag, tag)
            if garde in GARDEES:
                self._tampon.append("<%s>" % garde)

    def handle_endtag(self, tag):
        if tag in LISTES:
            self._pousser(self._genre_courant())
            if self._listes:
                self._listes.pop()
        elif tag in BLOCS:
            genre = self._genre_courant()
            numero = None
            if genre == "numero" and self._listes:
                numero = self._listes[-1][1]
            texte = "".join(self._tampon).strip()
            texte = re.sub(r"\s+", " ", texte)
            self._tampon = []
            if texte:
                self.blocs.append((genre, texte) if numero is None
                                  else ("numero", "%d. %s" % (numero, texte)))
            if self._pile and self._pile[-1] == tag:
                self._pile.pop()
            elif tag in self._pile:
                # balisage bancal : on referme jusqu'à la bonne, plutôt que de
                # laisser la pile mentir sur le genre des blocs suivants
                while self._pile and self._pile.pop() != tag:
                    pass
        elif tag in EN_LIGNE:
            garde = EQUIVALENTS.get(tag, tag)
            if garde in GARDEES and garde != "br":
                self._tampon.append("</%s>" % garde)

    def handle_data(self, data):
        self._tampon.append(_html.escape(data, quote=False))

    def close(self):
        super().close()
        self._pousser(self._genre_courant())


def blocs(html_source):
    """Le HTML rendu en blocs (genre, texte en ligne).

    Rend une liste vide sur une entrée vide, jamais `None` : l'appelant
    itère toujours.
    """
    if not html_source:
        return []
    lecteur = _Lecture()
    lecteur.feed(html_source)
    lecteur.close()
    return lecteur.blocs


def texte_nu(html_source):
    """Le même contenu, sans aucune balise — pour mesurer ou pour un résumé."""
    return " ".join(re.sub(r"<[^>]+>", "", t) for _g, t in blocs(html_source))
