"""Vérification d'un tapotement signé par une puce NTAG 424 DNA.

La puce ajoute elle-même à l'URL, à chaque tapotement, deux morceaux :

* ``picc_data`` : 16 octets chiffrés en AES-128-CBC (IV nul) avec la clé de
  lecture des métadonnées. En clair : un octet de forme, l'UID sur 7 octets, et
  un **compteur de lecture sur 3 octets** qui ne remonte jamais.
* ``cmac`` : 8 octets, la moitié d'un CMAC calculé avec une clé de session
  dérivée de l'UID et du compteur.

C'est la seule façon honnête de faire agir une pastille sans compte : le
porteur ne prouve rien, la puce prouve tout. Un clone ne peut pas produire le
CMAC sans la clé, et une URL recopiée porte un compteur déjà vu.

✅ **Vérifié contre le vecteur public d'AN12196** (clés à zéro,
``picc_data=EF963FF7828658A599F3041510671E88`` → UID ``04DE5F1EACC040``,
compteur 61, ``cmac=94EED9EE65337086``). L'essai est dans ``tests/test_sdm.py``
et il est joué à chaque passe : une implémentation cryptographique qu'on ne
rejoue pas est une implémentation qu'on croit.

🔴 La troncature du CMAC n'est pas « les 8 premiers octets ». Ce sont les octets
de rang impair (1, 3, 5, … 15). Se tromper là rend une vérification qui échoue
toujours, ce qui se lit comme une mauvaise clé.
"""
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.cmac import CMAC

IV_NUL = b"\x00" * 16
# AN12196 §Secure Dynamic Messaging : le vecteur de session pour le MAC.
PREFIXE_SV2 = b"\x3c\xc3\x00\x01\x00\x80"


class SdmInvalide(Exception):
    """Le tapotement ne porte pas une signature valide."""


def _cle(valeur):
    """Une clé AES-128 à partir de 32 caractères hexadécimaux."""
    if not valeur:
        raise SdmInvalide("Aucune clé de pastille signée n'est configurée.")
    brut = valeur.strip().replace(" ", "")
    try:
        octets = bytes.fromhex(brut)
    except ValueError:
        raise SdmInvalide("La clé n'est pas de l'hexadécimal.")
    if len(octets) != 16:
        raise SdmInvalide("Une clé AES-128 fait 16 octets (32 caractères).")
    return octets


def lire_picc(cle_meta, picc_hex):
    """Rend (uid_hex, compteur) à partir du bloc chiffré de la puce."""
    try:
        chiffre = bytes.fromhex((picc_hex or "").strip())
    except ValueError:
        raise SdmInvalide("picc_data n'est pas de l'hexadécimal.")
    if len(chiffre) != 16:
        raise SdmInvalide("picc_data doit faire 16 octets.")
    dechiffreur = Cipher(algorithms.AES(_cle(cle_meta)), modes.CBC(IV_NUL)).decryptor()
    clair = dechiffreur.update(chiffre) + dechiffreur.finalize()
    # L'octet de forme dit ce que la puce a mis dans le bloc : bit 7 l'UID,
    # bit 6 le compteur, et les quatre bits de poids faible la longueur de
    # l'UID. Sans les deux premiers, il n'y a rien à vérifier.
    forme = clair[0]
    if not forme & 0x80:
        raise SdmInvalide("Ce bloc ne porte pas d'UID.")
    if not forme & 0x40:
        raise SdmInvalide("Ce bloc ne porte pas de compteur de lecture.")
    longueur = forme & 0x0F
    if longueur not in (4, 7, 10):
        raise SdmInvalide("Longueur d'UID inattendue : %d octets." % longueur)
    uid = clair[1:1 + longueur]
    compteur = int.from_bytes(clair[1 + longueur:1 + longueur + 3], "little")
    return uid.hex().upper(), compteur


def verifier_cmac(cle_fichier, uid_hex, compteur, cmac_hex, message=b""):
    """Vrai quand le CMAC correspond à cet UID et à ce compteur."""
    try:
        attendu_brut = bytes.fromhex((cmac_hex or "").strip())
    except ValueError:
        raise SdmInvalide("cmac n'est pas de l'hexadécimal.")
    if len(attendu_brut) != 8:
        raise SdmInvalide("Le CMAC tronqué fait 8 octets.")
    uid = bytes.fromhex(uid_hex)
    sv2 = PREFIXE_SV2 + uid + int(compteur).to_bytes(3, "little")
    session = CMAC(algorithms.AES(_cle(cle_fichier)))
    session.update(sv2)
    cle_session = session.finalize()
    corps = CMAC(algorithms.AES(cle_session))
    corps.update(message)
    complet = corps.finalize()
    tronque = bytes(complet[i] for i in range(1, 16, 2))
    # Comparaison à temps constant : une comparaison naïve fuit, octet par
    # octet, de quoi fabriquer un CMAC valide par essais successifs.
    ecart = 0
    for a, b in zip(tronque, attendu_brut):
        ecart |= a ^ b
    return ecart == 0
