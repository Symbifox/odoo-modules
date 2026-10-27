from odoo import models


class HrExpense(models.Model):
    """Ce qu'une note sans écriture coûtera au budget, et quand.

    Ne sert qu'aux notes qu'aucune écriture vivante ne porte : soumises, ou
    approuvées dont l'écriture a été supprimée. Dès qu'une écriture existe, le
    budget lit ses lignes, qui font foi.
    """

    _inherit = "hr.expense"

    def _bf_budget_date(self):
        """La date que portera l'écriture, celle qui décide de l'exercice.

        Payée par l'employé : la date comptable de la pièce, ou celle qu'Odoo lui
        donnera s'il l'approuve aujourd'hui. Payée par la société : chaque note
        produit son propre paiement, daté de la note.
        """
        self.ensure_one()
        sheet = self.sheet_id
        if self.payment_mode == "company_account" or not sheet:
            return self.date
        return sheet.accounting_date or sheet._calculate_default_accounting_date()

    def _bf_budget_amount(self):
        """Ce qui tombera sur le compte de charge, en devise de la société.

        Le total d'une note est taxes comprises. Les taxes récupérables partent
        sur leurs propres comptes et ne sont pas une charge ; une taxe non
        récupérable (répartition sans compte) reste sur le compte de charge. On
        passe par le moteur de taxes d'Odoo, comme le fait la comptabilisation,
        plutôt que de soustraire le montant de taxe : la soustraction rendrait la
        taxe non récupérable invisible au budget.
        """
        self.ensure_one()
        if not self.tax_ids:
            return self.total_amount
        AccountTax = self.env["account.tax"]
        account = self._get_base_account()
        if self.payment_mode == "own_account":
            # ⚠️ Payée par l'employé, la facture est en devise de la SOCIÉTÉ
            # (`hr.expense.sheet._compute_currency_id`) : les taxes s'y calculent
            # sur le total converti. Les calculer en devise de la note, puis
            # convertir, s'écarte de l'écriture d'un ou deux cents.
            base_line = self._prepare_base_line_for_taxes_computation(
                price_unit=self.total_amount,
                quantity=1.0,
                account_id=account,
                currency_id=self.company_currency_id,
                rate=1.0,
            )
        else:
            # Payée par la société : le paiement est dans la devise de la note,
            # exactement comme le construit `_prepare_payments_vals`.
            rate = abs(self.total_amount_currency / self.total_amount) if self.total_amount else 0.0
            base_line = self._prepare_base_line_for_taxes_computation(
                price_unit=self.total_amount_currency,
                quantity=1.0,
                account_id=account,
                rate=rate,
            )
        base_lines = [base_line]
        company = self.company_id
        AccountTax._add_tax_details_in_base_lines(base_lines, company)
        AccountTax._round_base_lines_tax_details(base_lines, company)
        AccountTax._add_accounting_data_in_base_lines_tax_details(base_lines, company)
        tax_results = AccountTax._prepare_tax_lines(base_lines, company)
        elsewhere = sum(
            tax_line["balance"]
            for tax_line in tax_results["tax_lines_to_add"]
            if tax_line["account_id"] != account.id
        )
        return self.total_amount - elsewhere
