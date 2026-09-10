# -*- coding: utf-8 -*-
"""Un repas payé dans une autre devise.

Les attentes sont écrites **sans dépendre du taux** : ce sont des invariants
comptables, pas des chiffres recopiés d'un banc. Un taux qui change ne doit
pas faire échouer un test qui ne parle pas de taux.
"""

from odoo import Command
from odoo.tests import tagged

from .common import BancPourboire


@tagged("post_install", "-at_install", "bf_expense_tip")
class TestMultidevise(BancPourboire):

    def _depense_devise(self, mode):
        depense = self.env["hr.expense"].create({
            "name": "Dîner à l'étranger",
            "employee_id": self.expense_employee.id,
            "product_id": self.repas.id,
            "currency_id": self.other_currency.id,
            "total_amount_currency": 26.30,
            "tip_amount_currency": 3.10,
            "payment_mode": mode,
            "company_id": self.company_data["company"].id,
        })
        self.assertTrue(depense.is_multiple_currency, "le banc doit être multi-devise")
        return depense

    def test_l_assiette_suit_dans_les_deux_devises(self):
        depense = self._depense_devise("own_account")
        # Les taxes de la devise d'origine sont celles du reçu.
        self.assertAlmostEqual(depense.tax_amount_currency, 3.02, places=2)
        # Et le pourboire converti reste proportionnel au total converti.
        self.assertAlmostEqual(
            depense.tip_amount / depense.total_amount,
            3.10 / 26.30, places=4)

    def test_facture_fournisseur_en_devise(self):
        """⚠️ La facture fournisseur est en devise de la SOCIÉTÉ.

        `_prepare_bills_vals` convertit tout : le reçu est en HRK, l'écriture
        en USD. La ligne de pourboire doit donc porter `tip_amount`, converti,
        et non le montant du reçu.
        """
        depense = self._depense_devise("own_account")
        feuille = self._rapport(depense)
        ecriture = feuille.account_move_ids
        self.assertEqual(ecriture.currency_id, depense.company_currency_id)

        charges = self._charges(ecriture)
        self.assertEqual(len(charges), 2)
        pourboire = charges.filtered(lambda l: not l.tax_ids)
        self.assertAlmostEqual(pourboire.balance, depense.tip_amount, places=2)
        self.assertAlmostEqual(pourboire.amount_currency, depense.tip_amount, places=2)

        self.assertAlmostEqual(sum(ecriture.line_ids.mapped("balance")), 0.0, places=2)
        self.assertAlmostEqual(
            sum(ecriture.line_ids.mapped("amount_currency")), 0.0, places=2)

        # Le repas, c'est ce qui reste une fois le pourboire et les taxes
        # retirés du total. On lit les taxes sur les LIGNES, pas sur la
        # dépense : voir le test de l'écart d'un sou plus bas.
        taxes = ecriture.line_ids.filtered("tax_line_id")
        repas = charges.filtered("tax_ids")
        self.assertAlmostEqual(
            repas.balance,
            depense.total_amount - depense.tip_amount - sum(taxes.mapped("balance")),
            places=2)

    def test_carte_de_compagnie_en_devise(self):
        """L'écriture de paiement, elle, reste en devise du reçu."""
        depense = self._depense_devise("company_account")
        feuille = self._rapport(depense)
        ecriture = feuille.account_move_ids
        self.assertEqual(ecriture.currency_id, self.other_currency)

        # 1. L'écriture balance dans les deux colonnes.
        self.assertAlmostEqual(sum(ecriture.line_ids.mapped("balance")), 0.0, places=2)
        self.assertAlmostEqual(
            sum(ecriture.line_ids.mapped("amount_currency")), 0.0, places=2)

        # 2. La contrepartie vaut le total réellement payé.
        contrepartie = ecriture.line_ids.filtered(
            lambda l: l.account_id.account_type != "expense")
        self.assertAlmostEqual(
            sum(contrepartie.mapped("amount_currency")), -26.30, places=2)
        self.assertAlmostEqual(
            sum(contrepartie.mapped("balance")), -depense.total_amount, places=2)

        # 3. Le pourboire porte le montant du reçu, converti, sans taxe.
        charges = self._charges(ecriture)
        self.assertEqual(len(charges), 2)
        pourboire = charges.filtered(lambda l: not l.tax_ids)
        self.assertAlmostEqual(pourboire.amount_currency, 3.10, places=2)
        self.assertAlmostEqual(pourboire.balance, depense.tip_amount, places=2)

        # 4. Le repas vaut le total moins le pourboire moins les taxes.
        taxes = ecriture.line_ids.filtered("tax_line_id")
        repas = charges.filtered("tax_ids")
        self.assertAlmostEqual(
            repas.amount_currency,
            26.30 - 3.10 - depense.tax_amount_currency, places=2)
        self.assertAlmostEqual(
            repas.balance,
            depense.total_amount - depense.tip_amount - sum(taxes.mapped("balance")),
            places=2)

    def test_les_taxes_de_la_depense_et_celles_de_l_ecriture_peuvent_differer_d_un_sou(self):
        """Comportement du NOYAU, épinglé pour qu'il ne surprenne personne.

        `hr.expense.tax_amount` convertit le total puis calcule les taxes ;
        l'écriture convertit chaque ligne de taxe séparément. Les deux
        arrondis ne tombent pas toujours au même endroit. L'écriture reste
        juste — c'est elle qui balance — mais un contrôle qui comparerait
        `tax_amount` à la somme des lignes échouerait un jour sur un sou.
        """
        depense = self._depense_devise("company_account")
        feuille = self._rapport(depense)
        taxes = feuille.account_move_ids.line_ids.filtered("tax_line_id")
        # ⚠️ Arrondir AVANT de comparer : `abs(1.52 - 1.51)` vaut
        # 0.010000000000000009 en binaire, et un `<= 0.01` échoue dessus.
        ecart = round(abs(sum(taxes.mapped("balance")) - depense.tax_amount), 2)
        self.assertLessEqual(ecart, 0.01)
        self.assertGreater(ecart, 0.0, "sinon ce test n'épingle rien")
