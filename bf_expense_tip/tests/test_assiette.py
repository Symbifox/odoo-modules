# -*- coding: utf-8 -*-
"""Le chiffre qui justifie le module : les taxes du reçu, pas celles du total."""

from odoo.tests import tagged

from .common import BancPourboire


@tagged("post_install", "-at_install", "bf_expense_tip")
class TestAssiette(BancPourboire):

    def test_le_pourboire_sort_de_l_assiette(self):
        """26,30 $ dont 3,10 $ de pourboire : 3,02 $ de taxes, pas 3,42 $."""
        depense = self._depense()
        self.assertAlmostEqual(depense.total_amount_currency, 26.30, places=2)
        self.assertAlmostEqual(depense.tax_amount_currency, 3.02, places=2)
        self.assertAlmostEqual(depense.tax_amount, 3.02, places=2)

    def test_sans_le_module_l_assiette_serait_fausse(self):
        """Le témoin : le même reçu sans pourboire déclaré surtaxe de 0,40 $."""
        temoin = self._depense(pourboire=0.0)
        self.assertAlmostEqual(temoin.tax_amount_currency, 3.42, places=2)
        self.assertAlmostEqual(
            temoin.tax_amount_currency - self._depense().tax_amount_currency,
            0.40, places=2)

    def test_hors_taxes_plus_taxes_egale_le_total(self):
        """L'identité que `hr.expense.sheet` suppose déjà doit tenir.

        Le pourboire se range du côté hors taxes, parce qu'il n'en porte pas.
        """
        depense = self._depense()
        self.assertAlmostEqual(
            depense.untaxed_amount_currency + depense.tax_amount_currency,
            depense.total_amount_currency, places=2)
        # 26,30 − 3,02 = 23,28 : le repas net (20,18) plus le pourboire (3,10).
        self.assertAlmostEqual(depense.untaxed_amount_currency, 23.28, places=2)

    def test_le_pourboire_se_recalcule_quand_le_total_change(self):
        depense = self._depense()
        depense.total_amount_currency = 46.30
        self.assertAlmostEqual(depense.tax_amount_currency, 5.63, places=2)

    def test_un_pourboire_ajoute_apres_coup_refait_les_taxes(self):
        depense = self._depense(pourboire=0.0)
        self.assertAlmostEqual(depense.tax_amount_currency, 3.42, places=2)
        depense.tip_amount_currency = 3.10
        self.assertAlmostEqual(depense.tax_amount_currency, 3.02, places=2)

    def test_un_pourboire_retire_rend_les_taxes_du_noyau(self):
        depense = self._depense()
        depense.tip_amount_currency = 0.0
        self.assertAlmostEqual(depense.tax_amount_currency, 3.42, places=2)
