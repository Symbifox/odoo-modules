# -*- coding: utf-8 -*-
"""Largeur du texte en Lexend, mesurée une fois, partagée par les deux rendus.

Le repli des libellés se décide ICI et se range dans le plan. Si l'écran
repliait de son côté et le PDF du sien, les deux dessins divergeraient sur
n'importe quel nom un peu long, et personne ne le verrait avant d'imprimer.

`reportlab` est une dépendance d'Odoo (elle sert déjà aux rapports), donc
mesurer avec elle n'ajoute rien à l'image.
"""
import threading
from pathlib import Path

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

POLICES = Path(__file__).resolve().parent.parent / "static" / "fonts"
REGULIER = "BfOrgChartLexend"
GRAS = "BfOrgChartLexend-SemiBold"

_verrou = threading.Lock()
_enregistrees = False


def enregistrer_polices():
    """Enregistre Lexend auprès de reportlab, une seule fois par processus.

    ⚠️ Odoo sert plusieurs requêtes en parallèle dans le même processus :
    l'enregistrement est sous verrou, et le drapeau est relu dedans.
    """
    global _enregistrees
    if _enregistrees:
        return
    with _verrou:
        if _enregistrees:
            return
        pdfmetrics.registerFont(TTFont(REGULIER, str(POLICES / "Lexend-Regular.ttf")))
        pdfmetrics.registerFont(TTFont(GRAS, str(POLICES / "Lexend-SemiBold.ttf")))
        _enregistrees = True


def largeur(texte, taille, gras=False):
    enregistrer_polices()
    return pdfmetrics.stringWidth(texte or "", GRAS if gras else REGULIER, taille)


def replier(texte, taille, largeur_max, lignes_max=2, gras=False):
    """Replie un libellé sur au plus `lignes_max` lignes.

    Ce qui dépasse se termine par une ellipse. Un mot plus large que la boîte
    est coupé en caractères plutôt que de déborder : un nom de société comme
    « Investissements Transcontinentaux » doit tenir, même mal.
    """
    texte = (texte or "").strip()
    if not texte:
        return []
    mots, lignes, courante = texte.split(), [], ""
    for mot in mots:
        essai = (courante + " " + mot).strip()
        if largeur(essai, taille, gras) <= largeur_max or not courante:
            if largeur(essai, taille, gras) > largeur_max and not courante:
                # Mot seul trop large : on le coupe au caractère.
                morceau = ""
                for car in mot:
                    if largeur(morceau + car, taille, gras) > largeur_max and morceau:
                        lignes.append(morceau)
                        morceau = car
                        if len(lignes) == lignes_max:
                            return _ellipser(lignes, morceau, taille, largeur_max, gras)
                    else:
                        morceau += car
                courante = morceau
                continue
            courante = essai
        else:
            lignes.append(courante)
            courante = mot
            if len(lignes) == lignes_max:
                return _ellipser(lignes, courante, taille, largeur_max, gras)
    if courante:
        lignes.append(courante)
    return lignes[:lignes_max]


def _ellipser(lignes, reste, taille, largeur_max, gras):
    """La dernière ligne retenue absorbe ce qui reste, en ellipse."""
    if not lignes:
        return []
    derniere = lignes[-1]
    if not reste:
        return lignes
    while derniere and largeur(derniere + "…", taille, gras) > largeur_max:
        derniere = derniere[:-1]
    lignes[-1] = (derniere + "…") if derniere else "…"
    return lignes
