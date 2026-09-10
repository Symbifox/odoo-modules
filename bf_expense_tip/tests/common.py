# -*- coding: utf-8 -*-
"""Un banc québécois : TPS 5 % et TVQ 9,975 %, groupées, taxe incluse.

C'est la configuration réelle d'une PME du Québec — la taxe groupée
« 14,975 % TPS+TVQ » de `l10n_ca` — et c'est là que le pourboire fait mal :
le total du reçu est réputé porter les deux taxes.
"""

from odoo import Command
from odoo.addons.hr_expense.tests.common import TestExpenseCommon


class BancPourboire(TestExpenseCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        societe = cls.company_data["company"]

        cls.tps = cls.env["account.tax"].create({
            "name": "TPS 5 %",
            "amount_type": "percent",
            "amount": 5.0,
            "type_tax_use": "purchase",
            "company_id": societe.id,
        })
        cls.tvq = cls.env["account.tax"].create({
            "name": "TVQ 9,975 %",
            "amount_type": "percent",
            "amount": 9.975,
            "type_tax_use": "purchase",
            "company_id": societe.id,
        })
        cls.taxes_qc = cls.env["account.tax"].create({
            "name": "14,975 % TPS+TVQ",
            "amount_type": "group",
            "type_tax_use": "purchase",
            "company_id": societe.id,
            "children_tax_ids": [Command.set((cls.tps + cls.tvq).ids)],
        })

        # ⚠️ Une taxe créée nue n'a AUCUNE étiquette de répartition. Un
        # contrôle « le pourboire ne porte pas d'étiquette de taxe » passerait
        # alors tout seul, sans rien vérifier. On en pose donc de vraies.
        cls.etiquette_base = cls.env["account.account.tag"].create({
            "name": "Base des achats taxables (essai)",
            "applicability": "taxes",
            "country_id": societe.account_fiscal_country_id.id,
        })
        for taxe in (cls.tps, cls.tvq):
            taxe.invoice_repartition_line_ids.filtered(
                lambda l: l.repartition_type == "base"
            ).tag_ids = [Command.set(cls.etiquette_base.ids)]

        cls.repas = cls.env["product.product"].create({
            "name": "Repas d'affaires",
            "type": "service",
            "can_be_expensed": True,
            "standard_price": 0.0,
            "supplier_taxes_id": [Command.set(cls.taxes_qc.ids)],
            "property_account_expense_id": cls.company_data[
                "default_account_expense"].id,
        })
        cls.compte_pourboires = cls.env["account.account"].create({
            "name": "Pourboires",
            "code": "POURB",
            "account_type": "expense",
            "company_ids": [Command.set(societe.ids)],
        })

    def _depense(self, total=26.30, pourboire=3.10, mode="own_account"):
        return self.env["hr.expense"].create({
            "name": "Dîner avec un client",
            "employee_id": self.expense_employee.id,
            "product_id": self.repas.id,
            "total_amount_currency": total,
            "tip_amount_currency": pourboire,
            "payment_mode": mode,
            "company_id": self.company_data["company"].id,
        })

    def _rapport(self, depense):
        feuille = self.env["hr.expense.sheet"].create({
            "name": "Représentation",
            "employee_id": depense.employee_id.id,
            "expense_line_ids": [Command.set(depense.ids)],
            "company_id": depense.company_id.id,
        })
        feuille.action_submit_sheet()
        feuille._do_approve()
        feuille.action_sheet_move_post()
        return feuille

    @staticmethod
    def _charges(ecriture):
        """Les lignes de CHARGE, lignes de taxe exclues.

        ⚠️ Dans le plan comptable d'essai, les comptes de taxe sont eux aussi
        de type `expense` : filtrer sur le seul type de compte ramène les
        lignes de TPS et de TVQ et fait mentir le décompte.
        """
        return ecriture.line_ids.filtered(
            lambda l: l.account_id.account_type == "expense" and not l.tax_line_id)
