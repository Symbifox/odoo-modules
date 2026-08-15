# -*- coding: utf-8 -*-
"""Mesure du texte, côté serveur, sans moteur typographique.

Deux hauteurs de la carte dépendent de la largeur du texte : celle d'une
annotation, qui dépend du nombre de lignes une fois le texte replié, et celle
des bandeaux de pools externes, qui dépend du plus long nom de participant.
Le moteur de rendu les mesure avec la police ; le serveur, lui, n'a pas de
police à interroger. Il a la table.

Ce que fait ce module reproduit exactement ce que fait le moteur :

    text_length(s, corps) == somme des avances des caractères × corps

Ce n'est pas une approximation de la mesure, c'est la même somme sur les mêmes
nombres — les largeurs de `lexend_metriques` sont celles que le moteur a
rendues, relevées à leur valeur exacte. Une carte mesurée ici et la même carte
mesurée par le générateur tombent sur la même hauteur.

Un caractère hors table fait lever `MesureImpossible`, avec son nom. C'est la
même règle que `GeometrieIncomplete` : refuser franchement vaut mieux que
poser une largeur plausible et laisser la carte dériver en silence.

Bibliothèque standard seulement.
"""
import unicodedata

from .lexend_metriques import (AVANCES_GRAS, AVANCES_REGULIER, SUB_GRAS,
                               SUB_REGULIER)

# Corps et interlignes du moteur de rendu. Les changer ici sans les changer
# là-bas ferait diverger deux mesures qui doivent rester la même.
NOTE_SZ = 8.6
NOTE_INTER = 1.32
NOTE_HAUT, NOTE_BAS = 14.0, 6.0
NOTE_PAD = 20.0
EXT_SZ = 9.4
EXT_PAD = 16.0
EXT_MIN, EXT_MAX = 62.0, 132.0


class MesureImpossible(Exception):
    """Un caractère n'est pas dans la table étalonnée, et deviner serait pire."""


def _deplier(groupes):
    """La table groupée par largeur redevient caractère → largeur."""
    return {c: largeur for largeur, chaine in groupes.items() for c in chaine}


# Les substituts d'abord, la police par-dessus : ce que Lexend porte vraiment
# l'emporte toujours sur ce qu'une police de repli aurait rendu.
_REGULIER = dict(_deplier(SUB_REGULIER))
_REGULIER.update(_deplier(AVANCES_REGULIER))
_GRAS = dict(_deplier(SUB_GRAS))
_GRAS.update(_deplier(AVANCES_GRAS))


def _nommer(c):
    """De quoi retrouver le caractère fautif sans le voir à l'écran."""
    try:
        nom = unicodedata.name(c)
    except ValueError:
        nom = "sans nom"
    return "U+%04X (%s)" % (ord(c), nom)


def text_length(texte, taille, gras=False):
    """Largeur du texte au corps donné, comme le moteur la rendrait."""
    table = _GRAS if gras else _REGULIER
    total = 0.0
    for c in texte or "":
        try:
            total += table[c]
        except KeyError:
            raise MesureImpossible(
                "Le caractère %s n'est pas dans la table de largeurs de Lexend."
                " La carte ne peut pas être mesurée sans deviner."
                % _nommer(c)) from None
    return total * taille


def wrap(texte, taille, largeur, gras=False):
    """Replie le texte dans la largeur, mot à mot. Même découpe que le moteur."""
    mots, lignes, courante = (texte or "").split(), [], ""
    for mot in mots:
        essai = (courante + " " + mot).strip()
        if text_length(essai, taille, gras) <= largeur or not courante:
            courante = essai
        else:
            lignes.append(courante)
            courante = mot
    if courante:
        lignes.append(courante)
    return lignes


def hauteur_annotation(texte, largeur):
    """Hauteur d'une annotation : ce que son texte occupe, une fois replié."""
    lignes = wrap(texte, NOTE_SZ, largeur - NOTE_PAD)
    return NOTE_HAUT + len(lignes) * NOTE_SZ * NOTE_INTER + NOTE_BAS


def hauteur_pools_externes(noms):
    """Hauteur des bandeaux de pools externes : le plus long nom commande.

    Le nom est écrit à la verticale dans le bandeau, donc c'est sa longueur
    qui décide de l'épaisseur de la bande. Bornée des deux côtés : en dessous
    de 62 la boîte noire ne se lit plus, au-dessus de 132 elle mange la page.
    """
    noms = [n for n in (noms or []) if n] or [""]
    plus_long = max(text_length(n, EXT_SZ, gras=True) for n in noms)
    return min(EXT_MAX, max(EXT_MIN, plus_long + EXT_PAD))
