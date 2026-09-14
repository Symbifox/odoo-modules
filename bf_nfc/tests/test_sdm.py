"""La vérification cryptographique, rejouée contre le vecteur public d'AN12196.

🔴 Une implémentation cryptographique qu'on ne rejoue pas est une implémentation
qu'on croit. Ce vecteur vient de la note d'application de NXP (clés à zéro,
celles que porte une puce sortie d'usine) : s'il casse, c'est que quelqu'un a
touché à la dérivation de clé de session ou à la troncature, et les deux
échouent en silence par un simple « signature invalide ».
"""
from odoo.tests import TransactionCase, tagged

from ..models.sdm import SdmInvalide, lire_picc, verifier_cmac

CLE_USINE = "00" * 16
PICC = "EF963FF7828658A599F3041510671E88"
CMAC = "94EED9EE65337086"
UID = "04DE5F1EACC040"
COMPTEUR = 61


@tagged("post_install", "-at_install")
class TestSdm(TransactionCase):

    def test_vecteur_public_an12196(self):
        uid, compteur = lire_picc(CLE_USINE, PICC)
        self.assertEqual(uid, UID)
        self.assertEqual(compteur, COMPTEUR)
        self.assertTrue(verifier_cmac(CLE_USINE, uid, compteur, CMAC))

    def test_cmac_altere_refuse(self):
        self.assertFalse(verifier_cmac(CLE_USINE, UID, COMPTEUR, "94EED9EE65337087"))

    def test_compteur_different_refuse(self):
        """Le CMAC couvre le compteur : un rejeu sur un autre compteur ne passe pas."""
        self.assertFalse(verifier_cmac(CLE_USINE, UID, COMPTEUR + 1, CMAC))

    def test_mauvaise_cle_refuse(self):
        self.assertFalse(verifier_cmac("11" * 16, UID, COMPTEUR, CMAC))

    def test_entrees_malformees(self):
        with self.assertRaises(SdmInvalide):
            lire_picc(CLE_USINE, "pas-de-l-hexa")
        with self.assertRaises(SdmInvalide):
            lire_picc(CLE_USINE, "EF96")
        with self.assertRaises(SdmInvalide):
            lire_picc("", PICC)
        with self.assertRaises(SdmInvalide):
            verifier_cmac(CLE_USINE, UID, COMPTEUR, "1234")

    def test_bloc_chiffre_avec_une_autre_cle_ne_se_lit_pas(self):
        """Un picc_data déchiffré avec la mauvaise clé rend du bruit, pas un UID.

        ⚠️ Le refus doit venir de l'octet de forme, pas d'un UID fantaisiste :
        sans ce contrôle, on chercherait une pastille pour un UID inventé et on
        répondrait « pastille inconnue » au lieu de « clé inconnue ».

        ⚠️ Cette garde-là est probabiliste : du bruit passe l'octet de forme une
        fois sur dix environ. Ce qui garantit vraiment le refus, c'est le CMAC,
        éprouvé juste au-dessus. L'octet de forme ne sert qu'à rendre la bonne
        phrase à l'écran.
        """
        with self.assertRaises(SdmInvalide):
            lire_picc("22" * 16, PICC)
