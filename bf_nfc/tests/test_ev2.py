"""Les vecteurs publiés par NXP, rejoués contre notre fabrique de commandes.

🔴 **C'est le seul contrôle qui ne se trompe pas avec nous.** La puce de papier
(``faux_ntag424.py``) est écrite à partir du même document que le code qu'elle
éprouve : si j'ai mal lu AN12196, les deux côtés se tromperont ensemble et tout
passera au vert. Ces vecteurs-ci viennent du document lui-même, avec ses octets.

Source : AN12196, révision 1.8 du 17 novembre 2020, tables 19, 20, 26 et 27.
"""
from odoo.tests import TransactionCase, tagged

from ..models import ev2


@tagged("post_install", "-at_install")
class TestEv2(TransactionCase):

    def test_cles_de_session_table_20(self):
        """L'authentification avec la clé d'usine, et les deux clés qui en sortent."""
        cle = bytes(16)
        rnda = bytes.fromhex("B98F4C50CF1C2E084FD150E33992B048")
        rndb = bytes.fromhex("91517975190DCEA6104948EFA3085C1B")
        reponse = ev2.chiffrer(
            cle, bytes.fromhex("7614281A") + rnda[1:] + rnda[:1] + bytes(12)
        ).hex() + "9100"

        session = ev2.session_depuis(cle, rnda, rndb, reponse)

        self.assertEqual(session["ti"], "7614281A")
        self.assertEqual(session["chiffrement"], "7A93D6571E4B180FCA6AC90C9A7488D4")
        self.assertEqual(session["mac"], "FC4AF159B62E549B5812394CAB1918CC")

    def test_aleas_qui_ne_reviennent_pas_refuses(self):
        """Une puce qui ne rend pas notre aléa n'ouvre pas de session.

        Sans ce contrôle, n'importe quoi qui répond 32 octets ouvre une session,
        et le relais devient un oracle pour qui tient le téléphone.
        """
        cle = bytes(16)
        rnda, rndb = bytes(range(16)), bytes(range(16, 32))
        reponse = ev2.chiffrer(cle, bytes.fromhex("7614281A") + bytes(28)).hex() + "9100"
        with self.assertRaises(ev2.Ev2Invalide):
            ev2.session_depuis(cle, rnda, rndb, reponse)

    def test_reglages_sdm_table_19(self):
        """La commande qui allume la signature, octet pour octet."""
        session = {"ti": "9D00C4DF", "compteur": 1,
                   "chiffrement": "1309C877509E5A215007FF0ED19CA564",
                   "mac": "4C6626F5E72EA694202139295C7A7FC7"}

        apdu = ev2.apdu_reglages_sdm(session, {"picc_data": 0x20, "cmac": 0x43})

        self.assertEqual(
            apdu,
            "905F0000190261B6D97903566E84C3AE5274467E89EAD799B7C1A0EF7A0400")
        self.assertEqual(session["compteur"], 2,
                         "Le compteur de commandes doit avancer, sinon le MAC suivant tombe.")

    def test_changement_de_cle_table_26(self):
        """Changer une autre clé que celle qui a ouvert la session."""
        session = {"ti": "7614281A", "compteur": 2,
                   "chiffrement": "4CF3CB41A22583A61E89B158D252FC53",
                   "mac": "5529860B2FC5FB6154B7F28361D30BF9"}

        apdu = ev2.apdu_changer_cle(
            session, 2, bytes.fromhex("F3847D627727ED3BC9C4CC050489B966"),
            ancienne=bytes(16), version=1)

        self.assertEqual(
            apdu,
            "90C4000029022CF362B7BF4311FF3BE1DAA295E8C68DE09050560D19B9E16C2393AE9CD1FAC7"
            "5D0CE20BCD1D06E600")

    def test_changement_de_la_cle_qui_a_ouvert_table_27(self):
        """Changer la clé 0 alors qu'on est entré par elle : le cas de la fin de gravure."""
        session = {"ti": "7614281A", "compteur": 3,
                   "chiffrement": "4CF3CB41A22583A61E89B158D252FC53",
                   "mac": "5529860B2FC5FB6154B7F28361D30BF9"}

        apdu = ev2.apdu_changer_cle(
            session, 0, bytes.fromhex("5004BF991F408672B1EF00F08F9E8647"), version=1)

        self.assertEqual(
            apdu,
            "90C400002900C0EB4DEEFEDDF0B513A03A95A75491818580503190D4D05053FF75668A01D6FD"
            "A6610234BDED643200")

    def test_changer_une_autre_cle_sans_l_ancienne_refuse(self):
        session = {"ti": "7614281A", "compteur": 1,
                   "chiffrement": "4CF3CB41A22583A61E89B158D252FC53",
                   "mac": "5529860B2FC5FB6154B7F28361D30BF9"}
        with self.assertRaises(ev2.Ev2Invalide):
            ev2.apdu_changer_cle(session, 2, bytes(range(16)))

    def test_les_decalages_se_comptent_dans_le_fichier(self):
        """Un décalage faux ne lève rien sur la puce : il se contrôle ici."""
        adresse = ev2.adresse_a_graver("https://exemple.test/nfc/s")
        fichier = ev2.fichier_ndef(adresse)
        positions = ev2.decalages(fichier, adresse)

        # Ce que la puce écrira, écrit à la main, doit retomber sur les marques.
        self.assertEqual(fichier[positions["picc_data"]:positions["picc_data"] + 32],
                         b"0" * 32)
        self.assertEqual(fichier[positions["cmac"]:positions["cmac"] + 16], b"0" * 16)
        self.assertEqual(fichier[1], len(fichier) - 2,
                         "Le fichier NDEF porte sa longueur en tête.")

    def test_une_adresse_sans_marques_est_refusee(self):
        with self.assertRaises(ev2.Ev2Invalide):
            ev2.decalages(ev2.fichier_ndef("https://exemple.test/nfc/s"), None)
