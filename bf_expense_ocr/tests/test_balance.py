# -*- coding: utf-8 -*-
"""Le garde-fou : quand le reçu ne balance pas, RIEN n'est posé.

C'est la décision de conception du module. Un reçu thermique se lit mal ; on ne
sait pas lequel des cinq nombres est faux, donc en préremplir un présenterait
une supposition comme un fait.
"""

from odoo.tests import tagged

from .common import BancLecture


@tagged("post_install", "-at_install", "bf_expense_ocr")
class TestBalance(BancLecture):

    def _doute(self, recu):
        d = self._depense_vierge()
        with self._passerelle(recu):
            self.assertFalse(d.action_ocr_scan())
        self.assertEqual(d.ocr_state, "doubt")
        self.assertAlmostEqual(d.total_amount_currency, 0.0, places=2)
        self.assertAlmostEqual(d.tip_amount_currency, 0.0, places=2)
        self.assertTrue(d.ocr_error_message, "le motif doit être lisible")
        self.assertTrue(d.ocr_raw_response, "l'extraction reste consultable")
        return d

    def test_un_recu_qui_ne_balance_pas_ne_pose_rien(self):
        self._doute(dict(self.RECU_JUSTE, total=41.30))

    def test_un_total_manquant_ne_pose_rien(self):
        self._doute(dict(self.RECU_JUSTE, total=None))

    def test_un_sous_total_manquant_ne_pose_rien(self):
        """Sans sous-total, il n'y a pas de contrôle possible."""
        self._doute(dict(self.RECU_JUSTE, subtotal=None))

    def test_un_total_negatif_ne_pose_rien(self):
        self._doute(dict(self.RECU_JUSTE, total=-26.30))

    def test_un_pourboire_negatif_ne_pose_rien(self):
        self._doute(dict(self.RECU_JUSTE, tip=-3.10, total=20.09))

    def test_le_detail_qui_depasse_le_total_ne_pose_rien(self):
        self._doute(dict(self.RECU_JUSTE, tip=None, subtotal=30.00))

    # ---- le pourboire déduit ----

    def test_un_pourboire_absent_se_deduit_du_residu(self):
        recu = dict(self.RECU_JUSTE, tip=None)  # 26,30 − (20,18+3,02) = 3,10
        d = self._depense_vierge()
        with self._passerelle(recu):
            self.assertTrue(d.action_ocr_scan())
        self.assertEqual(d.ocr_state, "done")
        self.assertAlmostEqual(d.tip_amount_currency, 3.10, places=2)

    def test_un_recu_sans_pourboire_donne_zero(self):
        recu = dict(self.RECU_JUSTE, tip=None, total=23.20)
        d = self._depense_vierge()
        with self._passerelle(recu):
            self.assertTrue(d.action_ocr_scan())
        self.assertAlmostEqual(d.tip_amount_currency, 0.0, places=2)
        self.assertAlmostEqual(d.total_amount_currency, 23.20, places=2)

    def test_un_residu_invraisemblable_n_est_pas_un_pourboire(self):
        """55 % du sous-total : c'est une erreur de lecture, pas un pourboire."""
        self._doute(dict(self.RECU_JUSTE, tip=None, total=34.30))

    def test_deux_sous_d_ecart_passent(self):
        """La TPS et la TVQ s'arrondissent séparément sur le reçu aussi."""
        d = self._depense_vierge()
        with self._passerelle(dict(self.RECU_JUSTE, total=26.32)):
            self.assertTrue(d.action_ocr_scan())
        self.assertAlmostEqual(d.total_amount_currency, 26.32, places=2)

    def test_trois_sous_d_ecart_ne_passent_pas(self):
        self._doute(dict(self.RECU_JUSTE, total=26.34))
