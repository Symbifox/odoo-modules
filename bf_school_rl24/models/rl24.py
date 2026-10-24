from collections import defaultdict
from datetime import date

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import float_compare, float_round

from odoo.addons.bf_school_billing.models.fee import split

#: Moved only by the computation and the buttons.
SYSTEM_FIELDS = {"amount", "state", "issued_on", "was_issued", "partner_id", "student_id", "year", "company_id"}
#: What a statement is born with: to review, never issued.
STATEMENT_DEFAULTS = {"state": "draft", "issued_on": False, "was_issued": False}


class SchoolRl24(models.Model):
    """What a payer paid for a child's child care in a calendar year.

    The amount the Relevé 24 needs (Revenu Québec, letter 17-038083-001: the total of the
    child-care fees paid by each parent, whatever the child's age, with no threshold).
    Counted on the payments of the contract invoices: only the lines marked as child care, in
    proportion. The money settles the contract's instalments in the order of the services, and
    counts in the year of the services it pays (same letter: "pour des services rendus dans
    cette année"), whenever it was paid.
    """

    _name = "bf.school.rl24"
    _description = "Child-care fees paid (Relevé 24)"
    _inherit = ["mail.thread"]
    _order = "year desc, partner_id, student_id"

    name = fields.Char(compute="_compute_name")
    year = fields.Integer("Calendar year", required=True, readonly=True, index=True)
    # 🔴 An integer year is summed and printed "2 026" in a grouped list.
    year_label = fields.Char("Year", compute="_compute_year_label", store=True)
    company_id = fields.Many2one("res.company", required=True, readonly=True, index=True)
    currency_id = fields.Many2one(related="company_id.currency_id")
    partner_id = fields.Many2one("res.partner", "Payer", required=True, readonly=True, index=True,
                                 ondelete="restrict")
    student_id = fields.Many2one("res.partner", "Child", required=True, readonly=True, ondelete="restrict")
    amount = fields.Monetary("Child-care fees paid", readonly=True, tracking=True)
    state = fields.Selection([("draft", "To review"), ("issued", "Issued")], default="draft",
                             required=True, readonly=True, tracking=True)
    issued_on = fields.Date(readonly=True, copy=False)
    was_issued = fields.Boolean(
        readonly=True, copy=False,
        help="Issued at least once: never deleted, even brought back to zero (a corrected slip).")
    amount_now = fields.Monetary("Paid today", compute="_compute_amount_now",
                                 help="The same amount, counted again on today's payments.")
    is_outdated = fields.Boolean("Changed since issued", compute="_compute_amount_now",
                                 search="_search_is_outdated")

    _sql_constraints = [
        ("rl24_unique", "UNIQUE(company_id, year, partner_id, student_id)",
         "One statement per payer, child and year."),
    ]

    @api.depends("year")
    def _compute_year_label(self):
        for statement in self:
            statement.year_label = str(statement.year) if statement.year else False

    @api.depends("year", "partner_id", "student_id")
    def _compute_name(self):
        for statement in self:
            statement.name = "%s, %s, %s" % (statement.year, statement.partner_id.name, statement.student_id.name)

    def _compute_amount_now(self):
        cache = {}
        for statement in self:
            key = (statement.company_id, statement.year)
            if key not in cache:
                cache[key] = self._school_paid(statement.company_id, statement.year)
            now = cache[key].get((statement.partner_id.id, statement.student_id.id), 0.0)
            statement.amount_now = now
            statement.is_outdated = statement.state == "issued" and bool(
                float_compare(now, statement.amount, precision_rounding=statement.currency_id.rounding or 0.01))

    def _search_is_outdated(self, operator, value):
        if operator not in ("=", "!=") or not isinstance(value, bool):
            raise UserError(_("Unsupported search."))
        issued = self.search([("state", "=", "issued")])
        # Not stored: counted again on the payments of now, not on what an earlier read cached.
        issued.invalidate_recordset(["amount_now", "is_outdated"])
        outdated = issued.filtered("is_outdated")
        return [("id", "in" if (operator == "=") == value else "not in", outdated.ids)]

    @api.model_create_multi
    def create(self, vals_list):
        # 🔴 The office could create an "issued" statement of any amount over RPC, shown on the
        # family's portal and exported: only the computation creates.
        if not self.env.su:
            raise UserError(_("A statement is created by the computation of its year."))
        return super().create(vals_list)

    def write(self, vals):
        # 🔴 `readonly` guards the screen only: by RPC, an amount could be rewritten by hand.
        if not self.env.su and SYSTEM_FIELDS & set(vals):
            raise UserError(_("A statement moves with its buttons and its computation."))
        return super().write(vals)

    # --- The computation ------------------------------------------------------------------

    @api.model
    def _school_rl24_entries(self, invoice, refunds):
        """[(service date, amount, child-care share)] of what a contract invoice still asks once
        its credit notes are applied, in the order of the services.

        The contract's own instalments, in proportion to this payer's share (the payment term
        merges the instalments already due when a contract is billed late). The admission fee
        is never child care. After a termination, only the months provided are services; what
        the school keeps beyond them is the penalty (Act s. 73), never child care either.
        """
        contract = invoice.school_contract_id
        # 🔴 The child care left once the credit notes are applied: a credit note of the child-care
        # line counted it anyway, at the share of the invoice (child care never paid on a slip). A
        # credit note only lowers the share, never raises it: a line not marked child care may
        # still have credited some.
        care = sum(invoice.invoice_line_ids.filtered("school_rl24").mapped("price_total"))
        care_left = max(0.0, care - sum(refunds.invoice_line_ids.filtered("school_rl24").mapped("price_total")))
        share = self.env["bf.school.billing.share"].sudo().search([("invoice_id", "=", invoice.id)], limit=1)
        if not share:
            asked = max(0.0, invoice.amount_total - sum(refunds.mapped("amount_total")))
            share_of_care = min(care / invoice.amount_total, care_left / asked) if asked else 0.0
            return [(invoice.invoice_date, asked, share_of_care)]
        raw = []
        admission = contract.admission_fee - contract._school_registration_billed()
        if admission > 0:
            raw.append((invoice.invoice_date, admission, False))
        raw += [(due, amount, True) for due, amount in contract._installments() if amount]
        parts = split(invoice.amount_total, [amount for _d, amount, _s in raw])
        services = sum(part for part, (_d, _a, is_service) in zip(parts, raw) if is_service)
        contract_services = contract.total_price - contract.admission_fee
        rendered = (contract._school_services_rendered() * services / contract_services
                    if contract_services else 0.0)
        # The credit notes take off the last services first.
        credited = sum(refunds.mapped("amount_total"))
        entries = []
        for part, (due, _a, is_service) in reversed(list(zip(parts, raw))):
            cancelled = min(part, credited)
            credited -= cancelled
            entries.append((due, part - cancelled, is_service))
        entries.reverse()
        left = sum(amount for _due, amount, is_service in entries if is_service)
        share_of_care = min(care / services, care_left / left) if left and services else 0.0
        result = []
        for due, amount, is_service in entries:
            if not is_service:
                result.append((due, amount, 0.0))
                continue
            provided = max(0.0, min(amount, rendered))
            rendered -= provided
            result.append((due, provided, share_of_care))
            if amount - provided > 0:
                result.append((due, amount - provided, 0.0))   # the penalty
        return result

    @api.model
    def _school_paid(self, company, year):
        """{(payer id, child id): child-care fees paid for the services of this calendar year}.

        Per invoice, the money the school kept (payments, less what it paid back) settles what
        the invoice still asks, in the order of the services; what settles the services of the
        year counts at their child-care share.
        """
        start, end = date(year, 1, 1), date(year, 12, 31)
        Move = self.env["account.move"].sudo()
        invoices = Move.search([
            ("school_contract_id.company_id", "=", company.id), ("move_type", "=", "out_invoice"),
            ("state", "=", "posted"), ("school_contract_id.date_start", "<=", end),
            "|", ("school_contract_id.date_end", ">=", start), ("invoice_date", ">=", start)])
        bursaries = self.env["bf.school.billing.share"].sudo().search(
            [("invoice_id", "in", invoices.ids), ("kind", "=", "bursary")]).invoice_id
        paid = defaultdict(float)
        # 🔴 A bursary fund created as a person got a statement.
        for invoice in invoices - bursaries:
            if not invoice.invoice_line_ids.filtered("school_rl24"):
                continue
            refunds = Move.search([("reversed_entry_id", "=", invoice.id), ("move_type", "=", "out_refund"),
                                   ("state", "=", "posted")])
            money = (invoice | refunds)._school_paid_amount()
            counted = 0.0
            for due, amount, share_of_care in self._school_rl24_entries(invoice, refunds):
                settled = max(0.0, min(money, amount))
                money -= settled
                if start <= due <= end:
                    counted += settled * share_of_care
            paid[(invoice.partner_id, invoice.school_contract_id.student_id)] += counted
        rounding = company.currency_id.rounding
        return {
            (payer.id, child.id): float_round(amount, precision_rounding=rounding)
            for (payer, child), amount in paid.items()
            # The slip goes to the person who paid: not a company, not the bursary fund.
            if not payer.is_company and float_round(amount, precision_rounding=rounding) > 0
        }

    @api.model
    def _school_compute(self, company, year):
        """Create or update the statements to review; an issued one is never rewritten."""
        if year > fields.Date.context_today(self).year:
            raise UserError(_("A statement is computed for a year that has begun."))
        amounts = self._school_paid(company, year)
        existing = self.sudo().search([("company_id", "=", company.id), ("year", "=", year)])
        by_key = {(s.partner_id.id, s.student_id.id): s for s in existing}
        for key, amount in amounts.items():
            statement = by_key.get(key)
            if not statement:
                # 🔴 Written: in sudo, Odoo drops the office's `default_*` context but still
                # applies their own ir.default, and a statement born "issued" skipped the review
                # and reached the family's portal.
                self.sudo().create({**STATEMENT_DEFAULTS, "company_id": company.id, "year": year,
                                    "partner_id": key[0], "student_id": key[1], "amount": amount})
            elif statement.state == "draft" and statement.amount != amount:
                statement.sudo().amount = amount
        # A draft whose payments vanished (cancelled, unreconciled) goes away, unless it was
        # issued once: then it stays, at zero, for the corrected slip.
        gone = existing.filtered(lambda s: s.state == "draft" and (s.partner_id.id, s.student_id.id) not in amounts)
        gone.filtered("was_issued").sudo().write({"amount": 0.0})
        (gone - gone.filtered("was_issued")).sudo().unlink()
        return self.search([("company_id", "=", company.id), ("year", "=", year)])

    # --- Buttons ----------------------------------------------------------------------------

    def action_issue(self):
        self.check_access("write")
        for statement in self:
            if statement.state != "draft":
                raise UserError(_("Only a statement to review is issued."))
        self.sudo().write({"state": "issued", "issued_on": fields.Date.context_today(self), "was_issued": True})
        return True

    def action_update(self):
        """An issued statement that no longer matches: back to review, at today's amount."""
        self.check_access("write")
        self.invalidate_recordset(["amount_now", "is_outdated"])
        for statement in self.filtered("is_outdated"):
            statement.sudo().write({"state": "draft", "amount": statement.amount_now, "issued_on": False})
            statement.message_post(body=_("Updated to the payments of today: issue it again and "
                                          "tell the family a corrected slip will follow."),
                                   message_type="notification", subtype_xmlid="mail.mt_note")
        return True
