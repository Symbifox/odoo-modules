# -*- coding: utf-8 -*-
"""Codes QR, en vectoriel, sans matricer d'image.

`qrcode` sait rendre une image, mais il faut Pillow pour ça et le résultat est
un raster : sur une affiche d'atelier tirée en grand format, un QR matricé
bavarde aux bords et un téléphone met plus longtemps à l'accrocher. On ne prend
donc de la bibliothèque que sa matrice de modules — ce qu'elle calcule vraiment
— et on pose des rectangles. Le QR reste net à n'importe quel agrandissement,
et le module n'ajoute aucune dépendance d'image.

Le niveau de correction est délibérément le plus bas : c'est une étiquette
posée sur du papier propre, pas sur un flanc de conteneur. Chaque cran de
correction en plus ajoute des modules, donc rétrécit chaque module à taille de
QR constante — et c'est la taille du module, pas la redondance, qui décide si
un téléphone accroche à bout de bras.
"""
import qrcode

# En dessous, un téléphone peine à bout de bras sur une impression laser.
# 0,3 mm ≈ 0,85 pt : c'est le seuil au-delà duquel on refuse de rapetisser.
MODULE_MINI = 0.85


def matrice(donnees, bordure=1):
    """La grille de modules du QR : des booléens, `True` = carré encré."""
    q = qrcode.QRCode(version=None, box_size=1, border=bordure,
                      error_correction=qrcode.constants.ERROR_CORRECT_L)
    q.add_data(donnees)
    q.make(fit=True)
    return q.get_matrix()


def cote_lisible(donnees, cote_voulu, bordure=1):
    """Le côté à donner au QR pour que ses modules restent scannables.

    Rend `(cote, module)`. On agrandit si le côté demandé donnerait des modules
    trop fins ; on ne rapetisse jamais en dessous. Un QR trop petit n'est pas
    un QR discret, c'est un QR qui ne sert à rien — et l'ennui est qu'il a
    l'air parfaitement normal sur l'écran de celui qui l'a produit.
    """
    n = len(matrice(donnees, bordure))
    module = cote_voulu / n
    if module < MODULE_MINI:
        return MODULE_MINI * n, MODULE_MINI
    return cote_voulu, module
