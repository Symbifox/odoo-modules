# -*- coding: utf-8 -*-
"""Les couleurs d'un dessin, écrites une seule fois.

Avant, chaque rendu portait sa propre copie de la palette : le SVG en
hexadécimal, le PDF en triplets RGB recopiés à la main. Deux copies d'une même
palette finissent par diverger, et celles-ci avaient divergé de la marque d'un
chiffre sur le bleu comme sur l'encre. Ici la palette s'écrit en hexadécimal,
et le PDF la convertit au moment de peindre.

⚠️ Le bleu de marque ne sert JAMAIS de couleur de texte : il habille les
contours et les bandeaux, l'encre reste l'anthracite. C'est ce qui permet de
laisser une société choisir son bleu sans rendre un organigramme illisible.
"""
import re

# La marque de la maison, qui n'est qu'un REPLI : une société qui a réglé ses
# couleurs obtient les siennes. Ces deux valeurs sont celles que la base porte
# sur `res.company` et que le site sert.
BLEU_DEFAUT = "#29ABE2"
ENCRE_DEFAUT = "#2E3132"

GRIS = "#6B7280"
FILET = "#D5D9DC"
PAPIER = "#FFFFFF"

# Les teintes d'état ne relèvent PAS de la marque : l'ambre dit « partiel » et
# le rouge dit « au-dessus de cent » sur toutes les instances. Seul l'accent
# bleu suit la société.
NEUTRE = ("#FFFFFF", "#C9CFD4")
AMBRE = ("#FDF6E7", "#D6991F")
VERT = ("#E9F7EE", "#1B8A4B")
ROUGE = ("#FDEEEC", "#C03A2B")

HEXA = re.compile(r"^#[0-9A-Fa-f]{6}$")

# Seuil AA du texte courant sur son fond (WCAG 1.4.3).
CONTRASTE_MIN = 4.5


def rgb(hexa):
    """Hexadécimal vers triplet 0..1, ce que reportlab attend."""
    brut = hexa.lstrip("#")
    return tuple(int(brut[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


def _canal(valeur):
    return valeur / 12.92 if valeur <= 0.03928 else ((valeur + 0.055) / 1.055) ** 2.4


def luminance(hexa):
    r, v, b = (_canal(c) for c in rgb(hexa))
    return 0.2126 * r + 0.7152 * v + 0.0722 * b


def contraste(avant, arriere):
    a, b = luminance(avant) + 0.05, luminance(arriere) + 0.05
    return max(a, b) / min(a, b)


def _hexa_valide(valeur):
    """⚠️ La validation est ICI, une seule fois. Vérifier seulement le « # »
    laissait passer un `#ZZZZZZ` venu de la base, que `rgb()` transforme en
    `ValueError` non attrapée, donc en 500 sur une route publique."""
    if valeur and isinstance(valeur, str) and HEXA.match(valeur.strip()):
        return valeur.strip().upper()
    return ""


def _eclaircir(hexa, part):
    """Mélange vers le blanc, pour dériver le fond d'une boîte accentuée du bleu
    de la société. Un fond figé jurerait avec un bleu qui n'est pas le nôtre."""
    return "#%02X%02X%02X" % tuple(
        int(round((c + (1.0 - c) * part) * 255)) for c in rgb(hexa))


class Palette:
    """Les couleurs d'un dessin, construites une fois et plus modifiées.

    `bleu` et `encre` viennent de la société quand elle les porte. Les deux sont
    validés, et l'encre doit en plus rester lisible : une société dont la
    couleur foncée est un bleu pâle obtiendrait un titre à 2,9:1 sur blanc, donc
    un dessin conforme à sa marque et illisible. Dans ce cas on garde l'encre de
    la maison plutôt que de rendre une page qu'on ne peut pas lire.
    """

    __slots__ = ("encre", "bleu", "gris", "filet", "papier", "teintes")

    def __init__(self, bleu=None, encre=None):
        self.papier = PAPIER
        self.gris = GRIS
        self.filet = FILET
        self.bleu = _hexa_valide(bleu) or BLEU_DEFAUT
        propose = _hexa_valide(encre)
        if propose and contraste(propose, self.papier) >= CONTRASTE_MIN:
            self.encre = propose
        else:
            self.encre = ENCRE_DEFAUT
        self.teintes = {
            "neutre": NEUTRE,
            "bleu": (_eclaircir(self.bleu, 0.92), self.bleu),
            "ambre": AMBRE,
            "vert": VERT,
            "rouge": ROUGE,
        }

    def teinte(self, nom):
        return self.teintes.get(nom, self.teintes["neutre"])

    def teintes_rgb(self):
        """La même table, prête pour reportlab."""
        return {nom: (rgb(fond), rgb(contour))
                for nom, (fond, contour) in self.teintes.items()}


PAR_DEFAUT = Palette()
