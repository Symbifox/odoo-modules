# -*- coding: utf-8 -*-
"""À pourboire nul, le module doit être invisible.

C'est la condition de sûreté : un module installé ne change rien aux dépenses
qui ne le concernent pas. Les valeurs attendues sont celles du noyau.
"""

from odoo.tests import tagged

from .common import BancPourboire


@tagged("post_install", "-at_install", "bf_expense_tip")
class TestSansPourboire(BancPourboire):

    def test_les_montants_sont_ceux_du_noyau(self):
        depense = self._depense(pourboire=0.0)
        self.assertAlmostEqual(depense.tax_amount_currency, 3.42, places=2)
        self.assertAlmostEqual(depense.untaxed_amount_currency, 22.88, places=2)
        self.assertAlmostEqual(depense.tip_amount, 0.0, places=2)

    def test_l_ecriture_garde_une_seule_ligne_de_charge(self):
        depense = self._depense(pourboire=0.0)
        feuille = self._rapport(depense)
        charges = self._charges(feuille.account_move_ids)
        self.assertEqual(len(charges), 1)
        self.assertAlmostEqual(charges.balance, 22.88, places=2)

    def test_carte_de_compagnie_sans_pourboire(self):
        depense = self._depense(pourboire=0.0, mode="company_account")
        feuille = self._rapport(depense)
        charges = self._charges(feuille.account_move_ids)
        self.assertEqual(len(charges), 1)
        self.assertAlmostEqual(
            sum(feuille.account_move_ids.line_ids.mapped("balance")), 0.0,
            places=2)
