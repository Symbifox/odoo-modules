# -*- coding: utf-8 -*-
"""Ce que le module refuse d'enregistrer."""

from odoo import Command
from odoo.exceptions import ValidationError
from odoo.tests import tagged

from .common import BancPourboire


@tagged("post_install", "-at_install", "bf_expense_tip")
class TestGardeFous(BancPourboire):

    def test_un_pourboire_negatif_est_refuse(self):
        with self.assertRaises(ValidationError):
            self._depense(pourboire=-1.0)

    def test_un_pourboire_plus_grand_que_le_total_est_refuse(self):
        with self.assertRaises(ValidationError):
            self._depense(total=26.30, pourboire=30.0)

    def test_un_pourboire_egal_au_total_passe(self):
        """Un reçu qui n'est QUE du pourboire n'est pas absurde."""
        depense = self._depense(total=5.00, pourboire=5.00)
        self.assertAlmostEqual(depense.tax_amount_currency, 0.0, places=2)

    def test_une_categorie_a_prix_fixe_refuse_le_pourboire(self):
        kilometrage = self.env["product.product"].create({
            "name": "Kilométrage",
            "type": "service",
            "can_be_expensed": True,
            "standard_price": 0.72,
            "property_account_expense_id": self.company_data[
                "default_account_expense"].id,
        })
        with self.assertRaises(ValidationError):
            self.env["hr.expense"].create({
                "name": "Déplacement",
                "employee_id": self.expense_employee.id,
                "product_id": kilometrage.id,
                "quantity": 100,
                "tip_amount_currency": 3.10,
                "company_id": self.company_data["company"].id,
            })

    def test_baisser_le_total_sous_le_pourboire_est_refuse(self):
        depense = self._depense()
        with self.assertRaises(ValidationError):
            depense.total_amount_currency = 2.00
