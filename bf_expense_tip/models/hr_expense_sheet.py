# -*- coding: utf-8 -*-
"""La facture fournisseur du rapport porte la ligne de pourboire en plus."""

from odoo import Command, models


class HrExpenseSheet(models.Model):
    _inherit = "hr.expense.sheet"

    def _prepare_bills_vals(self):
        """Mode « l'employé avance l'argent ».

        Le noyau pose une ligne par dépense. Chaque dépense qui porte un
        pourboire en pose donc deux : le repas, dont le prix a déjà été
        réduit par `hr.expense._prepare_move_lines_vals`, puis le pourboire
        sans taxes. La contrepartie au passif est calculée par
        `account.move` à partir des lignes, donc elle suit toute seule.
        """
        vals = super()._prepare_bills_vals()
        pourboires = self.expense_line_ids.filtered("tip_amount_currency")
        if pourboires:
            vals["line_ids"] = list(vals["line_ids"]) + [
                Command.create(expense._prepare_tip_move_line_vals())
                for expense in pourboires
            ]
        return vals
