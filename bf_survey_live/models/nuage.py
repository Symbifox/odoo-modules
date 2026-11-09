# -*- coding: utf-8 -*-
"""Compter les mots d'une salle, et dire de quelle taille les écrire.

Tout se passe au serveur : le navigateur reçoit des mots déjà comptés, avec leur
taille. Deux raisons de ne pas compter dans la page : la liste des mots outils
reste à la maison, et la même mesure se relit dans un essai Python.
"""

import math
import re

# Un jeton : des lettres, éventuellement reliées par une apostrophe ou un trait
# d'union. Pas de chiffres : « 2026 » n'est pas un mot de la salle, et un nuage
# de nombres ne dit rien.
JETON_RE = re.compile(r"[^\W\d_]+(?:['\-][^\W\d_]+)*")

# Les apostrophes que les claviers posent vraiment, ramenées à une seule.
APOSTROPHES = ("’", "ʼ", "´", "`")

# ⚠️ En français, l'article s'élide dans le mot : « l'équipe » compte pour
# « équipe », sinon « l'équipe » et « équipe » font deux mots au lieu d'un. On ne
# coupe QUE devant une élision connue : « aujourd'hui » reste entier.
ELISIONS = frozenset((
    "l", "d", "j", "n", "m", "t", "s", "c", "qu",
    "jusqu", "lorsqu", "puisqu", "quoiqu", "quelqu",
))

MOTS_OUTILS_FR = frozenset((
    "a", "ai", "aie", "ait", "alors", "au", "aucun", "aussi", "autre", "autres",
    "aux", "avait", "avant", "avec", "avoir", "bien", "car", "ce", "cela",
    "celle", "celles", "celui", "ces", "cet", "cette", "ceux", "chaque", "chez",
    "comme", "comment", "dans", "de", "dedans", "dehors", "depuis", "des",
    "deux", "dit", "doit", "donc", "dont", "du", "elle", "elles", "en",
    "encore", "est", "et", "etre", "eu", "faire", "fait", "faut", "il", "ils",
    "je", "la", "le", "les", "leur", "leurs", "lui", "ma", "mais", "me",
    "meme", "mes", "moi", "moins", "mon", "ne", "ni", "non", "nos", "notre",
    "nous", "on", "ont", "ou", "par", "pas", "peu", "peut", "plus", "pour",
    "pourquoi", "quand", "que", "quel", "quelle", "quelles", "quels", "qui",
    "quoi", "sa", "sans", "se", "sera", "serait", "ses", "si", "soit",
    "son", "sont", "sous", "sur", "ta", "tes", "toi", "ton", "tous", "tout",
    "toute", "toutes", "tres", "tu", "un", "une", "va", "vos", "votre", "vous",
    "y", "etait", "etaient", "ete", "etes", "suis", "sommes", "ont", "avons",
    "avez", "oui", "deja", "trop", "beaucoup",
    # Les mêmes avec leurs accents : le nuage ne déshabille pas les mots, donc la
    # liste doit porter les deux graphies.
    "à", "été", "être", "était", "étaient", "êtes", "déjà", "très", "même",
    "où", "ça", "là", "plutôt",
))

MOTS_OUTILS_EN = frozenset((
    "a", "about", "after", "all", "also", "am", "an", "and", "any", "are", "as",
    "at", "be", "because", "been", "before", "being", "but", "by", "can",
    "could", "did", "do", "does", "doing", "done", "for", "from", "get", "had",
    "has", "have", "he", "her", "here", "him", "his", "how", "i", "if", "in",
    "into", "is", "it", "its", "just", "like", "me", "more", "most", "much",
    "my", "no", "not", "of", "on", "one", "only", "or", "other", "our", "out",
    "over", "own", "same", "she", "should", "so", "some", "such", "than",
    "that", "the", "their", "them", "then", "there", "these", "they", "this",
    "those", "to", "too", "us", "very", "was", "we", "were", "what", "when",
    "where", "which", "while", "who", "why", "will", "with", "would", "you",
    "your", "yes",
))

MOTS_OUTILS = MOTS_OUTILS_FR | MOTS_OUTILS_EN

# Un mot d'une seule lettre ne porte rien à l'écran.
LONGUEUR_MIN = 2

# La taille est un facteur, pas une mesure en pixels : la page décide de son
# unité. Le plus gros mot va au maximum, et la surface d'un mot suit son compte
# (d'où la racine : doubler le compte double la surface, pas la hauteur).
TAILLE_MIN = 1.0
TAILLE_MAX = 3.4


def _sans_apostrophe_exotique(texte):
    for signe in APOSTROPHES:
        texte = texte.replace(signe, "'")
    return texte


def jetons(texte):
    """Les mots d'un texte, dans leur casse d'origine, élisions retirées.

    ⚠️ La casse est gardée ici et minusculée par l'appelant : c'est la seule
    façon d'écrire « Odoo » à l'écran tout en comptant « odoo » avec lui.
    """
    if not texte:
        return []
    trouves = []
    for brut in JETON_RE.findall(_sans_apostrophe_exotique(texte)):
        if "'" in brut:
            tete, _, reste = brut.partition("'")
            if tete.lower() in ELISIONS and reste:
                brut = reste
        trouves.append(brut)
    return trouves


def _mots_ecartes(ajouts):
    """Les mots outils, plus ceux que la question écarte en plus."""
    if not ajouts:
        return MOTS_OUTILS
    if isinstance(ajouts, str):
        ajouts = jetons(ajouts.replace(",", " "))
    ajouts = [mot.lower() for mot in ajouts if mot]
    return MOTS_OUTILS | set(ajouts)


def nuage(textes, limite=40, a_ecarter=None):
    """Les mots d'une salle, du plus fréquent au moins, avec leur taille.

    Rend une liste de `{"text", "count", "size"}`. La graphie affichée est celle
    que la salle a le plus écrite : « Odoo » et « odoo » comptent pour un seul
    mot, et c'est la forme la plus fréquente qui monte à l'écran.
    """
    ecartes = _mots_ecartes(a_ecarter)
    comptes = {}
    graphies = {}
    for texte in textes:
        for mot in jetons(texte or ""):
            cle = mot.lower()
            if len(cle) < LONGUEUR_MIN or cle in ecartes:
                continue
            comptes[cle] = comptes.get(cle, 0) + 1
            formes = graphies.setdefault(cle, {})
            formes[mot] = formes.get(mot, 0) + 1
    if not comptes:
        return []
    # ⚠️ Le tri secondaire est alphabétique, pas l'ordre d'arrivée : deux mots à
    # égalité doivent sortir dans le même ordre d'un appel à l'autre, sinon le
    # nuage se réarrange à chaque rafraîchissement sans qu'un mot ait bougé.
    ordonnes = sorted(comptes.items(), key=lambda paire: (-paire[1], paire[0]))[:limite]
    plus_haut = ordonnes[0][1]
    return [{
        "text": _graphie(cle, graphies),
        "count": compte,
        "size": round(TAILLE_MIN + (TAILLE_MAX - TAILLE_MIN) * math.sqrt(compte / plus_haut), 3),
    } for cle, compte in ordonnes]


def _graphie(cle, graphies):
    """La graphie que la salle a le plus écrite, à égalité la première en ordre."""
    formes = graphies.get(cle) or {}
    if not formes:
        return cle
    return sorted(formes.items(), key=lambda paire: (-paire[1], paire[0]))[0][0]
