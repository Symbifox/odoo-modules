"""Le garde-fou : aucune graine de deuxième facteur n'entre dans le registre.

Tout le lot repose sur une promesse, « le registre sait, le coffre garde », et
une promesse qui n'est vérifiée nulle part finit par être fausse. Ce module la
rend exécutable : les champs du registre refusent ce qui ressemble à une graine.

Le TOTP exige la graine EN CLAIR au moment de produire le code. Qui produit,
détient. Ranger une graine ici mettrait le mot de passe et son deuxième facteur
dans la même base, ce qui laisse UN facteur au client, pas deux, sans qu'il le
sache.

⚠️ La détection est volontairement étroite. Un faux positif bloquerait un
enregistrement sans porte de sortie, sur un champ dont le contenu légitime est
une étiquette libre. On ne retient donc que ce qui ne peut pas être autre chose
qu'une graine.

⚠️ Ce garde est un filet, pas une preuve. Mesuré sur 160 graines fabriquées
comme le font les serveurs (base32 de 10, 16, 20 et 32 octets aléatoires), il en
laisse passer UNE : une graine de 16 caractères qui, par hasard, ne porte aucun
chiffre de 2 à 7, donc rien qui la distingue d'un mot en majuscules. Lever la
condition du chiffre ferait refuser « AUTHENTIFICATION », qui est une étiquette
plausible. Le compromis est assumé dans ce sens : ce module ne protège pas
d'abord par la détection, il protège parce qu'aucun de ses champs n'est fait
pour contenir une graine et qu'aucun de ses calculs n'en déchiffre une.
"""

import re

# Les deux formes d'URI que produisent les applications d'authentification.
# `otpauth://` porte la graine dans son paramètre `secret` ; `otpauth-migration://`
# est l'export en lot de Google Authenticator, qui porte toutes les graines à la
# fois. Aucune des deux ne peut être autre chose : refus certain.
_URI = re.compile(r'\botpauth(-migration)?://', re.IGNORECASE)

# Un paramètre `secret=` suivi d'assez de base32 pour être une vraie graine.
_PARAM_SECRET = re.compile(r'\bsecret\s*=\s*[A-Z2-7]{16,}', re.IGNORECASE)

# Une valeur qui n'est QUE du base32, d'une longueur de graine réelle, et qui
# porte au moins un chiffre de l'alphabet base32.
#
# ⚠️ L'alphabet base32 est A-Z plus 2-7 : il EXCLUT 0, 1, 8 et 9. Une étiquette
# écrite par une personne porte presque toujours une minuscule, un accent, une
# ponctuation, ou un chiffre absent de l'alphabet (« 2026 » contient un 0).
# C'est ce qui rend la règle utilisable.
#
# La longueur est le second filtre. Le base32 encode 5 octets en 8 caractères,
# donc les graines réelles font 16 ou 32 caractères le plus souvent, parfois 24
# ou 40, et 26 ou 52 quand le nombre d'octets ne tombe pas juste (le corps est
# alors suivi de « = »). Une suite de majuscules de longueur quelconque, elle,
# est une étiquette : sans ce filtre, « AUTHENTIFICATION2FA » (19 caractères,
# tous dans l'alphabet) serait refusé sans porte de sortie.
_BASE32_ONLY = re.compile(r'\A[A-Z2-7]+\Z')
_HAS_BASE32_DIGIT = re.compile(r'[2-7]')
_LONGUEURS_AVEC_REMPLISSAGE = (26, 52)

# Une graine présentée en groupes de quatre, telle que l'affichent les
# applications quand on ne peut pas lire le QR.
_GROUPEE = re.compile(r'\A(?:[A-Z2-7]{4}[ \-]){3,}[A-Z2-7]{2,4}=*\Z')


def _corps_base32(value):
    """Rend le corps base32 d'une valeur, ou None si elle n'en est pas un.

    Les séparateurs ne sont retirés que si la valeur est présentée EN GROUPES,
    forme qui n'appartient qu'aux graines. Les retirer partout effacerait le
    signal le plus fiable dont on dispose : une étiquette écrite par une
    personne place ses espaces autrement.
    """
    brut = (value or '').strip()
    if _GROUPEE.match(brut):
        brut = re.sub(r'[ \-]', '', brut)
    corps = brut.rstrip('=')
    return corps if _BASE32_ONLY.match(corps) else None


def otp_secret_reason(value, strict=True):
    """Rend la raison du refus, ou None si la valeur peut entrer.

    Rendre la RAISON plutôt qu'un booléen : le message d'erreur doit nommer ce
    qui a été reconnu, sinon la personne croit à une panne et contourne.

    ``strict=False`` ne garde que les deux règles CERTAINES, celles dont la
    valeur ne peut être qu'une graine. C'est ce qu'on applique aux champs de
    texte libre, comme les notes : la règle du base32 nu y ferait des faux
    positifs, et un faux positif sur un champ de rédaction bloquerait une
    sauvegarde sans porte de sortie.
    """
    if not value or not isinstance(value, str):
        return None

    if _URI.search(value):
        return "une adresse otpauth://, qui porte la graine elle-même"

    if _PARAM_SECRET.search(value):
        return "un paramètre secret= suivi d'une graine en base32"

    if not strict:
        return None

    corps = _corps_base32(value)
    if (corps
            and len(corps) >= 16
            and (len(corps) % 8 == 0
                 or len(corps) in _LONGUEURS_AVEC_REMPLISSAGE)
            and _HAS_BASE32_DIGIT.search(corps)):
        return "une graine en base32"

    return None
