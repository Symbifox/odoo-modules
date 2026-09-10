# -*- coding: utf-8 -*-
"""Un reçu qui balance remplit la dépense."""

from odoo.tests import tagged

from .common import BancLecture


@tagged("post_install", "-at_install", "bf_expense_ocr")
class TestLecture(BancLecture):

    def test_le_recu_juste_remplit_tout(self):
        d = self._depense_vierge()
        with self._passerelle(self.RECU_JUSTE):
            self.assertTrue(d.action_ocr_scan())
        self.assertEqual(d.ocr_state, "done")
        self.assertAlmostEqual(d.total_amount_currency, 26.30, places=2)
        self.assertAlmostEqual(d.tip_amount_currency, 3.10, places=2)
        self.assertEqual(str(d.date), "2026-09-08")
        self.assertEqual(d.name, "Restaurant Le Continental")
        self.assertAlmostEqual(d.ocr_confidence, 0.93, places=2)
        self.assertFalse(d.ocr_error_message)

    def test_les_taxes_suivent_le_pourboire_retire(self):
        """Le bout qui relie les deux modules : l'assiette exclut le pourboire."""
        d = self._depense_vierge()
        with self._passerelle(self.RECU_JUSTE):
            d.action_ocr_scan()
        self.assertAlmostEqual(d.tax_amount_currency, 3.02, places=2)

    def test_une_description_ecrite_a_la_main_n_est_pas_ecrasee(self):
        d = self._depense_vierge(nom="Dîner avec la mairesse")
        with self._passerelle(self.RECU_JUSTE):
            d.action_ocr_scan()
        self.assertEqual(d.name, "Dîner avec la mairesse")
        self.assertAlmostEqual(d.total_amount_currency, 26.30, places=2)

    def test_un_nom_venu_du_fichier_est_remplace(self):
        d = self._depense_vierge(nom="IMG_4821")
        with self._passerelle(self.RECU_JUSTE):
            d.action_ocr_scan()
        self.assertEqual(d.name, "Restaurant Le Continental")

    def test_les_montants_ecrits_en_texte_sont_lus(self):
        """Le modèle rend parfois « 26,30 $ » au lieu de 26.30."""
        recu = dict(self.RECU_JUSTE, total="26,30 $", subtotal="20,18",
                    gst="1,01", qst="2,01", tip="3,10")
        d = self._depense_vierge()
        with self._passerelle(recu):
            d.action_ocr_scan()
        self.assertEqual(d.ocr_state, "done")
        self.assertAlmostEqual(d.total_amount_currency, 26.30, places=2)

    def test_la_photo_part_avec_son_type(self):
        """La passerelle doit recevoir le mimetype, sinon elle le renifle."""
        d = self._depense_vierge(mimetype="image/jpeg")
        with self._passerelle(self.RECU_JUSTE) as espion:
            d.action_ocr_scan()
        self.assertEqual(len(espion.appels), 1)
        self.assertEqual(espion.appels[0]["mime"], "image/jpeg")
        self.assertIn("untrusted DATA", espion.appels[0]["prompt"])

    def test_l_extraction_brute_est_conservee(self):
        d = self._depense_vierge()
        with self._passerelle(self.RECU_JUSTE):
            d.action_ocr_scan()
        self.assertIn("Le Continental", d.ocr_raw_response)
