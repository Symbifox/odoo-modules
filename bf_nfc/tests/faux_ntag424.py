"""Une NTAG 424 DNA de papier, qui répond aux commandes comme le ferait la puce.

Elle sert à éprouver la gravure sans silicium : le serveur croit parler à une
puce, elle applique vraiment ce qu'on lui demande (clés changées, réglages SDM
posés), et elle sait ensuite **émettre l'adresse signée** qu'une vraie puce
émettrait. Le tour est donc complet : on grave, puis on tape, et c'est la porte
``/nfc/s`` qui juge.

⛔ **Ce qu'elle ne prouve pas** : que le silicium se comporte ainsi. Elle est
écrite à partir d'AN12196, comme le code qu'elle éprouve, donc une erreur de
lecture du document se retrouverait des deux côtés et passerait inaperçue. Le
garde-fou est ailleurs : ``test_ev2.py`` rejoue les vecteurs **publiés par NXP**,
octet pour octet. La puce de papier vérifie la conversation, les vecteurs
vérifient la cryptographie.
"""
import os

from odoo.addons.bf_nfc.models import ev2


class FausseNtag424:
    """Assez de la puce pour jouer une gravure, et rien de plus."""

    def __init__(self, uid="04DE5F1EACC040", cles=None):
        self.uid = bytes.fromhex(uid)
        self.cles = list(cles) if cles else [bytes(16) for _ in range(5)]
        self.fichier = b""
        self.sdm = None
        self.compteur_lecture = 0
        self._rndb = None
        self._cle_auth = None
        self._session = None
        self.journal = []

    # ------------------------------------------------------------------
    # Le guichet
    # ------------------------------------------------------------------

    def transmettre(self, apdu_hex):
        apdu = bytes.fromhex(apdu_hex)
        self.journal.append(apdu_hex.upper())
        if apdu_hex.upper().startswith("00A4"):
            return "9000"
        instruction = apdu[1]
        corps = apdu[5:-1]  # entre Lc et Le
        if instruction == 0x71:
            return self._defi(corps)
        if instruction == 0xAF:
            return self._ouvrir_session(corps)
        if instruction == 0x8D:
            return self._ecrire(corps)
        if instruction == 0x5F:
            return self._reglages(corps)
        if instruction == 0xC4:
            return self._changer_cle(corps)
        return "911C"  # commande illégale

    # ------------------------------------------------------------------
    # Authentification
    # ------------------------------------------------------------------

    def _defi(self, corps):
        numero = corps[0]
        if numero >= len(self.cles):
            return "9140"
        self._cle_auth = numero
        self._rndb = os.urandom(16)
        return ev2.chiffrer(self.cles[numero], self._rndb).hex().upper() + "91AF"

    def _ouvrir_session(self, corps):
        cle = self.cles[self._cle_auth]
        clair = ev2.dechiffrer(cle, corps)
        rnda, rndb_tourne = clair[:16], clair[16:32]
        if rndb_tourne != self._rndb[1:] + self._rndb[:1]:
            self._session = None
            return "91AE"  # authentification refusée
        ti = os.urandom(4)
        reponse = ev2.chiffrer(cle, ti + rnda[1:] + rnda[:1] + bytes(12))
        self._session = {
            "ti": ti.hex().upper(),
            "chiffrement": ev2.session_depuis(
                cle, rnda, self._rndb, reponse.hex() + "9100")["chiffrement"],
            "mac": ev2.session_depuis(
                cle, rnda, self._rndb, reponse.hex() + "9100")["mac"],
            "compteur": 0,
        }
        return reponse.hex().upper() + "9100"

    # ------------------------------------------------------------------
    # Les commandes protégées
    # ------------------------------------------------------------------

    def _verifier_mac(self, instruction, donnees, mac_recu):
        session = self._session
        entete = bytes([instruction, session["compteur"] & 0xFF,
                        (session["compteur"] >> 8) & 0xFF]) + bytes.fromhex(session["ti"])
        attendu = ev2._tronquer(ev2._cmac(bytes.fromhex(session["mac"]), entete + donnees))
        return attendu == mac_recu

    def _mac_de_reponse(self, donnees=b""):
        session = self._session
        session["compteur"] += 1
        entete = bytes([0x00, session["compteur"] & 0xFF,
                        (session["compteur"] >> 8) & 0xFF]) + bytes.fromhex(session["ti"])
        return ev2._tronquer(
            ev2._cmac(bytes.fromhex(session["mac"]), entete + donnees)).hex().upper()

    def _iv(self):
        session = self._session
        graine = b"\xa5\x5a" + bytes.fromhex(session["ti"]) \
            + bytes([session["compteur"] & 0xFF, (session["compteur"] >> 8) & 0xFF]) \
            + b"\x00" * 8
        return ev2.chiffrer(bytes.fromhex(session["chiffrement"]), graine)

    def _ecrire(self, corps):
        """WriteData en clair : à clés d'usine, le fichier NDEF est libre en écriture."""
        if corps[0] != 0x02:
            return "91F0"
        longueur = int.from_bytes(corps[4:7], "little")
        self.fichier = corps[7:7 + longueur]
        return "9100"

    def _reglages(self, corps):
        if not self._session:
            return "91AE"
        entete, chiffre, mac = corps[:1], corps[1:-8], corps[-8:]
        if not self._verifier_mac(0x5F, entete + chiffre, mac):
            return "911E"  # intégrité refusée
        clair = ev2.dechiffrer(bytes.fromhex(self._session["chiffrement"]), chiffre,
                               self._iv())
        # options(1) droits(2) optmiroir(1) droitsmiroir(2) puis trois décalages
        self.sdm = {
            "options_miroir": clair[3],
            "cle_meta": (clair[5] >> 4) & 0x0F,
            "cle_fichier": clair[5] & 0x0F,
            "picc": int.from_bytes(clair[6:9], "little"),
            "mac_entree": int.from_bytes(clair[9:12], "little"),
            "mac": int.from_bytes(clair[12:15], "little"),
        }
        return self._mac_de_reponse() + "9100"

    def _changer_cle(self, corps):
        if not self._session:
            return "91AE"
        numero, chiffre, mac = corps[0], corps[1:-8], corps[-8:]
        if not self._verifier_mac(0xC4, bytes([numero]) + chiffre, mac):
            return "911E"
        clair = ev2.dechiffrer(bytes.fromhex(self._session["chiffrement"]), chiffre,
                               self._iv())
        if numero == self._cle_auth:
            self.cles[numero] = clair[:16]
            reponse = self._mac_de_reponse()
        else:
            neuve = bytes(a ^ b for a, b in zip(clair[:16], self.cles[numero]))
            import binascii
            crc = (binascii.crc32(neuve) ^ 0xFFFFFFFF).to_bytes(4, "little")
            if clair[17:21] != crc:
                return "911E"  # la puce n'a pas reconstitué la bonne clé
            self.cles[numero] = neuve
            reponse = self._mac_de_reponse()
        return reponse + "9100"

    # ------------------------------------------------------------------
    # Ce que la puce émet quand on l'approche d'un téléphone
    # ------------------------------------------------------------------

    def adresse_au_tapotement(self):
        """L'URL que le téléphone ouvrirait, signature fraîche comprise."""
        if not self.sdm:
            raise AssertionError("Cette puce n'a pas de signature à poser.")
        self.compteur_lecture += 1
        clair = bytes([0x80 | 0x40 | len(self.uid)]) + self.uid \
            + self.compteur_lecture.to_bytes(3, "little")
        clair += os.urandom(16 - len(clair))
        picc = ev2.chiffrer(self.cles[self.sdm["cle_meta"]], clair)

        sv2 = b"\x3c\xc3\x00\x01\x00\x80" + self.uid \
            + self.compteur_lecture.to_bytes(3, "little")
        session = ev2._cmac(self.cles[self.sdm["cle_fichier"]], sv2)
        cmac = ev2._tronquer(ev2._cmac(session, b""))

        # Les valeurs vont là où les décalages le disent, dans le fichier même.
        octets = bytearray(self.fichier)
        octets[self.sdm["picc"]:self.sdm["picc"] + 32] = picc.hex().upper().encode()
        octets[self.sdm["mac"]:self.sdm["mac"] + 16] = cmac.hex().upper().encode()
        return _url_du_fichier(bytes(octets))


def _url_du_fichier(fichier):
    """Relit l'URL d'un fichier NDEF, comme le ferait le téléphone."""
    message = fichier[2:]
    longueur = message[2]
    charge = message[4:4 + longueur]
    prefixes = dict(ev2.PREFIXES_URI)
    return prefixes.get(charge[0], "") + charge[1:].decode()
