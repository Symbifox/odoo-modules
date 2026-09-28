import base64
import io

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

from ..models.rendu import CouleurRefusee, verifier_couleurs
from .commun import monter

# Un PNG d'un pixel : assez pour qu'un logo « existe ».
PNG_1PX = base64.b64encode(bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082"))


@tagged("post_install", "-at_install")
class TestImpression(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        monter(cls)
        cls.Format = cls.env["bf.qr.label.format"]
        cls.carre = cls.env.ref("bf_qr_manager.format_letter_3274_1")
        cls.petit = cls.env.ref("bf_qr_manager.format_letter_5167")
        cls.adresse = cls.env.ref("bf_qr_manager.format_letter_5160")

    def _assistant(self, **valeurs):
        valeurs.setdefault("tag_ids", [(6, 0, self.etiquettes.ids)])
        return self.env["bf.qr.imprimer"].with_user(self.gestion).create(valeurs)

    def test_tous_les_formats_semes_tiennent_sur_la_feuille(self):
        formats = self.Format.search([])
        self.assertEqual(len(formats), 10)
        for fmt in formats:
            fmt._check_geometrie()
            self.assertGreater(fmt.qr_max_mm, 9, fmt.name)

    def test_grille_qui_deborde_refusee(self):
        with self.assertRaises(Exception):
            self.Format.create({"name": "Faux", "papier": "letter", "largeur": 100, "hauteur": 50,
                                "colonnes": 3, "lignes": 1, "pas_x": 100, "pas_y": 50})

    def test_le_pdf_compte_les_bonnes_feuilles(self):
        # 10 étiquettes sur des planches de 9 : 2 feuilles ; décalées de 8 : encore 2.
        from odoo.tools.pdf import PdfFileReader as PdfReader
        pdf = self._assistant(format_id=self.carre.id)._pdf()
        self.assertEqual(len(PdfReader(io.BytesIO(pdf)).pages), 2)
        pdf = self._assistant(format_id=self.carre.id, decalage=8)._pdf()
        self.assertEqual(len(PdfReader(io.BytesIO(pdf)).pages), 2)
        pdf = self._assistant(format_id=self.adresse.id)._pdf()
        self.assertEqual(len(PdfReader(io.BytesIO(pdf)).pages), 1)

    def test_decalage_hors_planche_refuse(self):
        with self.assertRaises(UserError):
            self._assistant(format_id=self.carre.id, decalage=9)._pdf()

    def test_logo_refuse_sur_un_petit_code(self):
        assistant = self._assistant(format_id=self.adresse.id, avec_logo=True, logo=PNG_1PX)
        self.assertLess(assistant.cote_mm, 25)
        self.assertIn("trop petit pour porter un logo", assistant.avertissement)
        with self.assertRaises(UserError):
            assistant._pdf()

    def test_logo_accepte_sur_un_grand_code(self):
        assistant = self._assistant(format_id=self.carre.id, avec_logo=True, logo=PNG_1PX)
        self.assertGreaterEqual(assistant.cote_mm, 25)
        self.assertTrue(assistant._pdf().startswith(b"%PDF"))

    def test_logo_svg_nomme(self):
        svg = base64.b64encode(b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"/>')
        with self.assertRaisesRegex(UserError, "SVG"):
            self._assistant(format_id=self.carre.id, avec_logo=True, logo=svg)._pdf()

    def test_petit_code_prevenu_mais_imprime(self):
        assistant = self._assistant(format_id=self.petit.id)
        self.assertIn("seulement de près", assistant.avertissement)
        self.assertTrue(assistant._pdf().startswith(b"%PDF"))

    def test_regles_de_couleur(self):
        self.assertEqual(verifier_couleurs("#1c1f20", "#ffffff"), ("#1c1f20", "#ffffff"))
        for code, fond in (("#29ABE1", "#FFFFFF"), ("#FFFFFF", "#000000"), ("bleu", "#FFFFFF")):
            with self.assertRaises(CouleurRefusee):
                verifier_couleurs(code, fond)
        assistant = self._assistant(format_id=self.carre.id, couleur_code="#29ABE1")
        self.assertIn("Contraste insuffisant", assistant.avertissement)
        with self.assertRaises(UserError):
            assistant._pdf()

    def test_l_interne_n_imprime_pas(self):
        assistant = self.env["bf.qr.imprimer"].with_user(self.interne).create(
            {"tag_ids": [(6, 0, self.etiquettes.ids)], "format_id": self.carre.id})
        with self.assertRaises(UserError):
            assistant._pdf()
