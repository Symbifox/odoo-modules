# -*- coding: utf-8 -*-
"""Ce qu'on peut poser sur un plan, et de quelle couleur.

Un seul endroit pour les natures de zones et d'éléments : le modèle, le
rendu à l'écran, le PDF et l'export diagrams.net lisent tous cette table.
"""

# (code, libellé, fond pastel). Le fond est pensé pour du papier blanc et
# une encre #2D3031 : le plan reste une feuille, quel que soit le thème.
GENRES_ZONE = [
    ("bureau", "Bureau fermé", "#DCEFF9"),
    ("reunion", "Salle de réunion", "#E7F6EC"),
    ("ouvert", "Aire ouverte", "#FFF8E1"),
    ("technique", "Local technique", "#FEECE9"),
    ("entreposage", "Entreposage", "#ECECEC"),
    ("accueil", "Accueil", "#F3E8F9"),
    ("repos", "Cuisine et repos", "#FFF1E0"),
    ("sanitaire", "Sanitaires", "#E0F5F8"),
    ("circulation", "Circulation", "#FAFAFA"),
    ("autre", "Autre", "#F4F4F4"),
]

# (code, libellé, largeur cm, hauteur cm). Les tailles sont celles d'un
# objet courant, pour qu'une forme posée soit déjà à l'échelle.
GENRES_ELEMENT = [
    ("poste", "Poste de travail", 160.0, 80.0),
    ("table", "Table", 180.0, 90.0),
    ("imprimante", "Imprimante", 60.0, 60.0),
    ("ecran", "Écran mural", 120.0, 15.0),
    ("commutateur", "Commutateur réseau", 45.0, 25.0),
    ("borne", "Borne Wi-Fi", 30.0, 30.0),
    ("serveur", "Serveur", 60.0, 80.0),
    ("baie", "Baie réseau", 60.0, 100.0),
    ("prise", "Prise réseau", 15.0, 15.0),
    ("telephone", "Téléphone", 20.0, 20.0),
    ("camera", "Caméra", 20.0, 20.0),
    ("autre", "Autre", 50.0, 50.0),
]

GENRES_LIEN = [
    ("reseau", "Réseau (cuivre)"),
    ("fibre", "Fibre optique"),
    ("electrique", "Électrique"),
    ("autre", "Autre"),
]

ROTATIONS = [("0", "0°"), ("90", "90°"), ("180", "180°"), ("270", "270°")]

# Ce que le plan peut signaler sur un élément. Le module seul n'en pose
# aucune ; un pont (hébergement, par exemple) en pose d'après ce qu'il sait.
TEINTES = ("", "alerte", "attention", "ok")


def selection_zones():
    return [(c, n) for c, n, _f in GENRES_ZONE]


def selection_elements():
    return [(c, n) for c, n, _w, _h in GENRES_ELEMENT]


def couleur_zone(code):
    return dict((c, f) for c, _n, f in GENRES_ZONE).get(code, "#F4F4F4")


def taille_element(code):
    return dict((c, (w, h)) for c, _n, w, h in GENRES_ELEMENT).get(code, (50.0, 50.0))


def libelle_zone(code):
    return dict(selection_zones()).get(code, code)


def libelle_element(code):
    return dict(selection_elements()).get(code, code)
