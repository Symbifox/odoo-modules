from collections import defaultdict
from datetime import date

from odoo import api, models
from odoo.tools import float_round


class SchoolRl24(models.Model):
    _inherit = "bf.school.rl24"

    @api.model
    def _school_paid(self, company, year):
        """The contract's child care, and that of the child-care invoices.

        A month's invoice counts in the year of the month served, whenever it is paid; a book of tickets
        in the year it is paid (its periods are not known when it is bought). On each invoice, the money
        kept (payments, less what went back) pays its lines in proportion: the child-care lines of each
        child count at that rate.
        """
        paid = defaultdict(float, super()._school_paid(company, year))
        start, end = date(year, 1, 1), date(year, 12, 31)
        Move = self.env["account.move"].sudo()
        monthly = Move.search([
            ("company_id", "=", company.id), ("move_type", "=", "out_invoice"), ("state", "=", "posted"),
            ("school_childcare_run_id.month", ">=", start), ("school_childcare_run_id.month", "<=", end)])
        books = self.env["bf.school.childcare.ticket_book"].sudo().search(
            [("company_id", "=", company.id), ("invoice_id.state", "=", "posted")]).invoice_id
        extra = defaultdict(float)
        for invoice in monthly | books:
            # 🔴 Only the credit notes of the invoice, and each one only lowers it: an accounting user who put
            # a negative child-care line on a credit note, or an invoice posing as a reversal, grew the
            # Relevé 24. The invoice's own lines are set by the billing alone.
            refunds = Move.search([("reversed_entry_id", "=", invoice.id), ("move_type", "=", "out_refund"),
                                   ("state", "=", "posted")])
            asked = invoice.amount_total - sum(refunds.mapped("amount_total"))
            if asked <= 0:
                continue
            # A book counts what was paid in the year; a month's invoice, all it received.
            money = self._school_money(invoice | refunds, *((start, end) if invoice in books else (None, None)))
            care = defaultdict(float)
            for line in invoice.invoice_line_ids.filtered(lambda l: l.school_rl24 and l.school_childcare_student_id):
                care[line.school_childcare_student_id] += max(0.0, line.price_total)
            for line in refunds.invoice_line_ids.filtered(lambda l: l.school_rl24 and l.school_childcare_student_id):
                if line.school_childcare_student_id in care:
                    care[line.school_childcare_student_id] -= max(0.0, line.price_total)
            # 🔴 Never more child care than the money received: a credit note of a line not ticked (a
            # discount) lowered what the invoice asked, and the child care left counted in full.
            total = sum(max(0.0, amount) for amount in care.values())
            base = max(asked, total)
            rate = max(0.0, min(1.0, money / base)) if base else 0.0
            for child, amount in care.items():
                extra[(invoice.partner_id, child)] += max(0.0, amount) * rate
        rounding = company.currency_id.rounding
        for (payer, child), amount in extra.items():
            if payer.is_company:
                continue
            paid[(payer.id, child.id)] += amount
        return {key: float_round(amount, precision_rounding=rounding) for key, amount in paid.items()
                if float_round(amount, precision_rounding=rounding) > 0}

    @api.model
    def _school_money(self, moves, start=None, end=None):
        """The money received on these invoices, net of what went back out on their credit notes; between
        `start` and `end` only when given.

        As `_school_paid_amount` of the contract billing, but 🔴 a credit note counts as money only when it
        credits ANOTHER invoice (money paid there, moved here): a credit note made by hand, credited to no
        invoice, cancels what is asked; it is not money.
        """
        def when(partials):
            return partials.filtered(lambda p: start <= p.max_date <= end) if start else partials

        paid = 0.0
        terms = moves.line_ids.filtered(lambda l: l.display_type == "payment_term")
        for line in terms.filtered(lambda l: l.move_id.move_type == "out_invoice"):
            for partial in when(line.matched_credit_ids):
                other = partial.credit_move_id.move_id
                if other.move_type == "out_refund":
                    moved = other.reversed_entry_id and other.reversed_entry_id != line.move_id
                    paid += partial.amount if moved else 0.0
                else:
                    paid += partial.amount * other._school_cash_share()
        for line in terms.filtered(lambda l: l.move_id.move_type == "out_refund"):
            for partial in when(line.matched_debit_ids):
                other = partial.debit_move_id.move_id
                if other.move_type == "out_invoice":
                    paid -= partial.amount if other != line.move_id.reversed_entry_id else 0.0
                else:
                    paid -= partial.amount * other._school_cash_share()
        return paid
