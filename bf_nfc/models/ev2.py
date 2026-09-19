"""Les commandes qu'on envoie à une NTAG 424 DNA pour la graver, fabriquées ici.

Le téléphone ne fait que transporter des octets : il approche la puce, envoie ce
que ce module a fabriqué, et renvoie ce que la puce a répondu. **La clé ne quitte
jamais le serveur.** C'est le seul arrangement qui ne mette pas la clé mère d'une
société dans un appareil mobile, et c'est aussi celui qui permettra plus tard de
prouver qu'une puce était là *maintenant*.

Le protocole vient d'**AN12196** de NXP. La séquence a été relue contre une
implémentation libre sous licence MIT (`luu176/NTAG424-SDM`), sans en recopier le
code : ce qui suit est écrit pour ce module, avec ses noms et ses gardes.

🔴 **Trois détails qui font échouer une gravure sans dire pourquoi** :

* Le CMAC tronqué, ce ne sont pas les 8 premiers octets, ce sont les octets de
  rang impair. La même règle que dans ``sdm.py``, et la même soirée perdue si on
  se trompe.
* Le compteur de commandes monte à **chaque commande MACée**, et il entre à la
  fois dans le MAC et dans le vecteur d'initialisation. Une commande qu'on
  fabrique sans l'envoyer désynchronise la session : on ne fabrique donc une
  commande qu'au moment de l'envoyer.
* Les décalages SDM se comptent **dans le fichier NDEF** (préfixe de longueur
  compris), et s'écrivent en **petit-boutiste**. Un décalage faux ne lève rien :
  la puce écrit sa signature à côté, et le serveur rend « signature invalide ».
"""
import os

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.cmac import CMAC

IV_NUL = b"\x00" * 16

# Sélection de l'application NDEF (D2760000850101), en mode « ne rien rendre ».
APDU_SELECTION = "00A4040C07D276000085010100"

# Réglages du fichier NDEF pour une pastille signée, tels que la porte /nfc/s
# les attend : miroir de PICCData chiffré et CMAC, lecture libre.
SDM_OPTIONS = 0x40           # SDM et miroirs activés, commande en clair
SDM_DROITS_FICHIER = "00E0"  # lecture libre, écriture et changement en clé 0
SDM_OPTIONS_MIROIR = 0xC1    # UID et compteur dans le bloc chiffré, plus l'encodage ASCII
# 🔴 Ces deux octets s'écrivent tels quels, PAS en petit-boutiste comme les
# décalages : RFU=F, compteur=clé 1, métadonnées=clé 2, fichier=clé 1. Ce sont
# les valeurs de l'exemple officiel d'AN12196 (table 19), et c'est pour cela que
# la clé de métadonnées de la maison va en clé 2 et la clé de fichier en clé 1.
SDM_DROITS_MIROIR = "F121"
CLE_METADONNEES = 2
CLE_FICHIER = 1

STATUT_OK = "9100"
STATUT_SUITE = "91AF"


class Ev2Invalide(Exception):
    """La puce n'a pas répondu ce que cette étape attendait."""


def _aes(cle, iv=IV_NUL):
    return Cipher(algorithms.AES(cle), modes.CBC(iv))


def chiffrer(cle, clair, iv=IV_NUL):
    c = _aes(cle, iv).encryptor()
    return c.update(clair) + c.finalize()


def dechiffrer(cle, chiffre, iv=IV_NUL):
    d = _aes(cle, iv).decryptor()
    return d.update(chiffre) + d.finalize()


def _cmac(cle, message):
    c = CMAC(algorithms.AES(cle))
    c.update(message)
    return c.finalize()


def _tronquer(mac):
    """Les octets de rang impair, comme partout dans cette famille de puces."""
    return bytes(mac[i] for i in range(1, 16, 2))


def _tourner_a_gauche(octets):
    return octets[1:] + octets[:1]


def statut(reponse_hex):
    """Les deux derniers octets d'une réponse, en majuscules."""
    reponse = (reponse_hex or "").strip().upper()
    if len(reponse) < 4:
        raise Ev2Invalide("La puce n'a rien répondu.")
    return reponse[-4:]


def _corps(reponse_hex):
    return bytes.fromhex(reponse_hex.strip()[:-4])


# ----------------------------------------------------------------------
# 1. L'authentification, en deux allers-retours
# ----------------------------------------------------------------------

def apdu_authentification(numero_cle=0):
    """Première commande : « je veux m'authentifier avec cette clé »."""
    return "9071000002%02X0000" % numero_cle


def repondre_au_defi(cle, reponse_hex, rnda=None):
    """Rend (commande, rnda, rndb) à partir du défi chiffré de la puce.

    La puce a envoyé son aléa chiffré. On le déchiffre, on tire le nôtre, et on
    renvoie les deux, le sien tourné d'un octet : c'est ce décalage qui prouve
    qu'on a vraiment déchiffré et pas recopié.
    """
    if statut(reponse_hex) != STATUT_SUITE:
        raise Ev2Invalide("La puce a refusé l'authentification (%s)." % statut(reponse_hex))
    rndb = dechiffrer(cle, _corps(reponse_hex))
    if len(rndb) != 16:
        raise Ev2Invalide("Le défi de la puce ne fait pas 16 octets.")
    rnda = rnda or os.urandom(16)
    envoi = chiffrer(cle, rnda + _tourner_a_gauche(rndb))
    return "90AF000020%s00" % envoi.hex().upper(), rnda, rndb


def session_depuis(cle, rnda, rndb, reponse_hex):
    """Les clés de session, une fois que la puce a confirmé.

    🔴 On vérifie que la puce nous rend bien NOTRE aléa tourné d'un octet. Sans ce
    contrôle, n'importe quoi qui répond 32 octets ouvrirait une session, et le
    relais deviendrait un oracle de chiffrement pour qui tient le téléphone.
    """
    if statut(reponse_hex) != STATUT_OK:
        raise Ev2Invalide("La puce a refusé notre réponse (%s)." % statut(reponse_hex))
    clair = dechiffrer(cle, _corps(reponse_hex))
    if len(clair) < 20:
        raise Ev2Invalide("La confirmation de la puce est trop courte.")
    ti, rnda_retour = clair[:4], clair[4:20]
    if rnda_retour != _tourner_a_gauche(rnda):
        raise Ev2Invalide("La puce n'a pas rendu notre aléa : clé ou puce inattendue.")

    prefixe = bytes([rnda[0], rnda[1]]) + bytes(a ^ b for a, b in zip(rnda[2:8], rndb[:6]))
    corps = prefixe + rndb[6:16] + rnda[8:16]
    return {
        "ti": ti.hex().upper(),
        "chiffrement": _cmac(cle, b"\xa5\x5a\x00\x01\x00\x80" + corps).hex().upper(),
        "mac": _cmac(cle, b"\x5a\xa5\x00\x01\x00\x80" + corps).hex().upper(),
        "compteur": 0,
    }


# ----------------------------------------------------------------------
# 2. La messagerie sécurisée : MAC de commande et vecteur d'initialisation
# ----------------------------------------------------------------------

def _entete_mac(commande, session):
    return bytes([commande, session["compteur"] & 0xFF, (session["compteur"] >> 8) & 0xFF]) \
        + bytes.fromhex(session["ti"])


def _mac_de_commande(commande, session, donnees):
    """⚠️ Consomme le compteur : à n'appeler qu'au moment d'envoyer la commande."""
    mac = _cmac(bytes.fromhex(session["mac"]), _entete_mac(commande, session) + donnees)
    session["compteur"] += 1
    return _tronquer(mac)


def _iv_d_envoi(session):
    graine = b"\xa5\x5a" + bytes.fromhex(session["ti"]) \
        + bytes([session["compteur"] & 0xFF, (session["compteur"] >> 8) & 0xFF]) + b"\x00" * 8
    return chiffrer(bytes.fromhex(session["chiffrement"]), graine)


# ----------------------------------------------------------------------
# 3. Le message NDEF, et les décalages que la puce devra remplir
# ----------------------------------------------------------------------

PREFIXES_URI = ((0x04, "https://"), (0x03, "http://"))


def _enregistrement_uri(url):
    """Un enregistrement NDEF de type URI, court, avec son abrégé de schéma."""
    code, reste = 0x00, url
    for valeur, prefixe in PREFIXES_URI:
        if url.startswith(prefixe):
            code, reste = valeur, url[len(prefixe):]
            break
    charge = bytes([code]) + reste.encode()
    if len(charge) > 254:
        raise Ev2Invalide("L'adresse est trop longue pour un enregistrement court.")
    return bytes([0xD1, 0x01, len(charge), 0x55]) + charge


def fichier_ndef(url):
    """Le contenu du fichier NDEF : sa longueur sur deux octets, puis le message."""
    message = _enregistrement_uri(url)
    return bytes([0x00, len(message)]) + message


def adresse_a_graver(base):
    """L'adresse gravée, avec la place que la puce remplira à chaque lecture."""
    return "%s?picc_data=%s&cmac=%s" % (base.rstrip("?&"), "0" * 32, "0" * 16)


def decalages(fichier, url):
    """Où commencent les deux valeurs dans le fichier, en comptant le préfixe."""
    trouves = {}
    for nom in ("picc_data", "cmac"):
        marque = (nom + "=").encode()
        position = fichier.find(marque)
        if position < 0:
            raise Ev2Invalide("L'adresse gravée ne porte pas « %s= »." % nom)
        trouves[nom] = position + len(marque)
    return trouves


def apdu_ecrire_ndef(fichier):
    """Écriture en clair du fichier NDEF : à clés d'usine, ce fichier est libre.

    🔴 C'est volontairement la PREMIÈRE écriture de la séquence. Une gravure
    interrompue ici laisse une puce à clés d'usine, que n'importe qui reprend,
    nous compris. L'ordre inverse laisserait une puce muette.
    """
    corps = bytes([0x02, 0x00, 0x00, 0x00, len(fichier) & 0xFF,
                   (len(fichier) >> 8) & 0xFF, 0x00]) + fichier
    return "908D0000%02X%s00" % (len(corps), corps.hex().upper())


# ----------------------------------------------------------------------
# 4. Les deux commandes protégées : réglages SDM, puis changement de clé
# ----------------------------------------------------------------------

def apdu_reglages_sdm(session, positions):
    """Allume la signature sur le fichier NDEF et dit où l'écrire."""
    def petit_boutiste(valeur):
        return bytes([valeur & 0xFF, (valeur >> 8) & 0xFF, (valeur >> 16) & 0xFF])

    clair = bytes([SDM_OPTIONS]) + bytes.fromhex(SDM_DROITS_FICHIER) \
        + bytes([SDM_OPTIONS_MIROIR]) + bytes.fromhex(SDM_DROITS_MIROIR) \
        + petit_boutiste(positions["picc_data"]) \
        + petit_boutiste(positions["cmac"]) \
        + petit_boutiste(positions["cmac"]) \
        + b"\x80"
    chiffre = chiffrer(bytes.fromhex(session["chiffrement"]), clair, _iv_d_envoi(session))
    mac = _mac_de_commande(0x5F, session, b"\x02" + chiffre)
    corps = b"\x02" + chiffre + mac
    return "905F0000%02X%s00" % (len(corps), corps.hex().upper())


def _crc_jam(octets):
    """Le CRC32 inversé que la puce attend, en petit-boutiste."""
    import binascii
    crc = binascii.crc32(octets) ^ 0xFFFFFFFF
    return crc.to_bytes(4, "little")


def apdu_changer_cle(session, numero, nouvelle, ancienne=None, version=1):
    """Remplace une clé de la puce. Dernière commande de la séquence.

    Pour la clé 0, la puce reçoit la nouvelle clé telle quelle. Pour les autres,
    elle reçoit le OU exclusif de l'ancienne et de la nouvelle, plus un CRC qui
    lui permet de vérifier qu'elle a bien reconstitué la bonne.
    """
    if numero == 0:
        clair = nouvelle + bytes([version]) + b"\x80" + b"\x00" * 14
    else:
        if not ancienne:
            raise Ev2Invalide("Changer une autre clé que la clé 0 exige l'ancienne.")
        melange = bytes(a ^ b for a, b in zip(nouvelle, ancienne))
        clair = melange + bytes([version]) + _crc_jam(nouvelle) + b"\x80" + b"\x00" * 10
    chiffre = chiffrer(bytes.fromhex(session["chiffrement"]), clair, _iv_d_envoi(session))
    donnees = bytes([numero]) + chiffre
    mac = _mac_de_commande(0xC4, session, donnees)
    corps = donnees + mac
    return "90C40000%02X%s00" % (len(corps), corps.hex().upper())
