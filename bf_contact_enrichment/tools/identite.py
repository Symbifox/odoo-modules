"""Comparer deux valeurs de coordonnées sans se fier à leur écriture.

Sert la garde d'identité de la maison  : un retour du pont qui
porte le mobile du propriétaire de la boîte doit être reconnu comme tel, que
le texte dise « 418 555-0199 », « +1 (418) 555-0199 » ou « 14185550199 ».

Pur Python (stdlib et ``matching``), donc éprouvable sans base.
"""
import re

from . import matching

#: Un numéro plus court que ça n'identifie personne (poste, code, année).
_CHIFFRES_UTILES = 7
#: On compare sur les dix derniers chiffres : le 1 d'un indicatif nord-américain
#: et un 00/+ international ne doivent pas faire deux numéros différents.
_CHIFFRES_COMPARES = 10


def numeros(valeur):
    """TOUS les numéros identifiables d'une valeur, en jeu normalisé.

    🔴 Un seul numéro par valeur ne suffit pas, et la queue de la
    concaténation de tous les chiffres est pire que rien : « 418 555-0188
    poste 221 » rendait ``5550188221``, qui ne ressemble plus au numéro de la
    même personne écrit sans son poste. Même perte pour « T 418 555-0188 /
    C 418 555-0199 », où seul le second survivait. On extrait donc chaque
    suite de chiffres séparément et on compare des ENSEMBLES.
    """
    trouves = set()
    for morceau in re.findall(r"\d(?:[\d\s().\-]*\d)?", valeur or ""):
        chiffres = re.sub(r"\D", "", morceau)
        if len(chiffres) < _CHIFFRES_UTILES:
            continue
        trouves.add(chiffres[-_CHIFFRES_COMPARES:])
        if len(chiffres) > _CHIFFRES_COMPARES:
            # ⚠️ Un poste collé au numéro sans mot — « 418 555-0188 221 » — se
            # lit comme UNE suite de chiffres : la queue ne ressemble alors
            # plus au numéro. On garde donc aussi la TÊTE.
            trouves.add(chiffres[:_CHIFFRES_COMPARES])
    return trouves


def numero(valeur):
    """Le premier numéro identifiable d'une valeur, ou ``""``.

    Gardé pour les comparaisons de valeur à valeur ; une comparaison contre
    l'identité de la maison passe par :func:`numeros`.
    """
    trouves = numeros(valeur)
    return sorted(trouves)[0] if trouves else ""


def adresse(valeur):
    """Une adresse de courriel comparable, ou ``""``."""
    texte = (valeur or "").strip().lower()
    # Une signature écrit parfois « Jean Tremblay <jean@x.ca> ».
    trouve = re.search(r"[^\s<>,;]+@[^\s<>,;]+", texte)
    return trouve.group(0).rstrip(".") if trouve else ""


def domaine(valeur):
    """Le domaine d'une adresse ou d'une URL, ``""`` si générique ou absent.

    ⚠️ Un domaine grand public (``gmail.com``…) ne désigne personne : le
    retenir ferait refuser le site de tout correspondant qui écrit de là.
    """
    # ⚠️ « Camille <camille@maison-essai.test> » : `extract_domain` coupe au
    # dernier `@` et garde le chevron fermant. L'adresse se dégage d'abord.
    texte = valeur or ""
    if "@" in texte:
        texte = adresse(texte) or texte
    brut = matching.extract_domain(texte) or ""
    brut = re.sub(r"^www\.", "", brut.strip().lower()).strip(".<>\"' ")
    if not brut or brut in matching.GENERIC_DOMAINS:
        return ""
    return brut


def nom(valeur):
    """Un nom de personne ou de société comparable, ou ``""``."""
    texte = matching.normalize(valeur or "")
    texte = re.sub(r"[.,;:!?/\\()\[\]{}\"'’]", " ", texte)
    return " ".join(texte.split())
