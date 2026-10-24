import logging
import math

from odoo import SUPERUSER_ID, Command, _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools import clean_context, float_is_zero

_logger = logging.getLogger(__name__)


class ChildcareTicketBook(models.Model):
    """A book of tickets bought by a family for one child and one school year: each ticket pays one
    occasional period. Not refundable."""

    _name = "bf.school.childcare.ticket_book"
    _description = "Book of child care tickets"
    _order = "create_date desc, id desc"

    student_id = fields.Many2one("res.partner", "Student", required=True, ondelete="restrict", index=True)
    school_id = fields.Many2one("bf.school", "School", required=True, ondelete="restrict", index=True)
    company_id = fields.Many2one(related="school_id.company_id", store=True, index=True)
    currency_id = fields.Many2one(related="company_id.currency_id")
    year_id = fields.Many2one("bf.school.year", "School year", required=True, ondelete="restrict", index=True)
    payer_id = fields.Many2one("res.partner", "Bought by", required=True, ondelete="restrict", index=True)
    tickets = fields.Integer(required=True)
    price = fields.Monetary(required=True)
    invoice_id = fields.Many2one("account.move", "Invoice", required=True, ondelete="restrict", index=True)
    state = fields.Selection([("to_pay", "To pay"), ("paid", "Paid"), ("cancelled", "Cancelled")],
                             default="to_pay", required=True, index=True)
    charge_ids = fields.One2many("bf.school.childcare.charge", "ticket_book_id", "Periods paid")
    used = fields.Integer("Tickets used", compute="_compute_used")
    remaining = fields.Integer("Tickets left", compute="_compute_used")
    usable = fields.Boolean("Tickets usable", compute="_compute_used",
                            help="Paid, and its invoice neither unpaid since nor credited.")
    invoice_name = fields.Char("Invoice number", compute="_compute_invoice_name")

    @api.depends("charge_ids", "state", "tickets", "invoice_id.payment_state", "invoice_id.reversal_move_ids.state")
    def _compute_used(self):
        for book in self:
            book.used = len(book.sudo().charge_ids)
            left = book._school_left()
            book.usable = left > 0
            book.remaining = left

    def _school_credited(self):
        self.ensure_one()
        return sum(self.sudo().invoice_id.reversal_move_ids.filtered(lambda m: m.state == "posted").mapped("amount_total"))

    def _school_left(self):
        """The tickets this book may still pay, counted now.

        🔴 A credit note leaves a paid invoice « paid »: the tickets credited are taken off, in proportion
        (half the price credited, half the tickets), never the ones already used.
        """
        self.ensure_one()
        book = self.sudo()
        if book.state != "paid" or book.invoice_id.payment_state not in ("paid", "in_payment"):
            return 0
        kept = max(0.0, book.price - book._school_credited())
        paid = book.tickets if not book.price else math.floor(book.tickets * kept / book.price + 1e-9)
        used = self.env["bf.school.childcare.charge"].sudo().search_count([("ticket_book_id", "=", book.id)])
        return max(0, paid - used)

    def _compute_invoice_name(self):
        # Read in sudo: the office has no access to the invoices themselves.
        for book in self:
            book.invoice_name = book.sudo().invoice_id.name

    @api.depends("student_id", "tickets", "year_id")
    def _compute_display_name(self):
        for book in self:
            book.display_name = _("%(count)s tickets · %(student)s · %(year)s", count=book.tickets,
                                  student=book.student_id.name or "", year=book.year_id.name or "")

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            raise UserError(_("A book of tickets is bought on the family portal."))
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su:
            raise UserError(_("A book of tickets changes with its payment."))
        return super().write(vals)

    def unlink(self):
        if not self.env.su:
            raise UserError(_("A book of tickets is cancelled, not deleted."))
        return super().unlink()

    def _school_lock(self):
        """Serialize the use of a book: two billings at once would spend its last ticket twice (see
        bf_school_meal: an UPDATE, not a row lock, under REPEATABLE READ)."""
        self.env.cr.execute("UPDATE bf_school_childcare_ticket_book SET write_date = now() at time zone 'UTC' "
                            "WHERE id IN %s", [tuple(self.ids)])

    # --- Buying -------------------------------------------------------------------------------------

    @api.model
    def _school_buy(self, partner, student):
        """An adult with parental authority buys a book for the child: an invoice to pay online."""
        if student not in partner._school_authority_links().student_id:
            raise AccessError(_("Buying tickets belongs to the adults with parental authority over the child."))
        today = student._childcare_local_today()
        school = student._childcare_school(today)
        if not school:
            raise UserError(_("The child does not attend child care at school."))
        year = self.env["bf.school.year"].sudo().search([("school_id", "=", school.id), ("date_start", "<=", today),
                                                          ("date_end", ">=", today)], limit=1)
        rate = self.env["bf.school.childcare.rate"]._school_rate(school, year, "ticket_book") if year else None
        if not rate:
            raise UserError(_("The school does not sell books of tickets."))
        # A book already asked and not paid is paid first: a second click is not a second invoice.
        unpaid = self.sudo().search([("payer_id", "=", partner.id), ("student_id", "=", student.id),
                                     ("year_id", "=", year.id), ("state", "=", "to_pay")])
        # 🔴 A book whose invoice was credited is over: offered again, it blocked the family.
        credited = unpaid.filtered(lambda b: b._school_credited())
        credited.write({"state": "cancelled"})
        if unpaid - credited:
            return (unpaid - credited)[:1]
        # 🔴 A caught error does not roll back the request (lesson of the bookings): all or nothing.
        with self.env.cr.savepoint():
            return self._school_create_book(partner, student, school, year, rate, today)

    @api.model
    def _school_create_book(self, partner, student, school, year, rate, today):
        lang = partner.lang or school.company_id.partner_id.lang or "fr_CA"
        # 🔴 The superuser in a clean context: the clicker's ir.default and `default_*` stay out of
        # the invoice (lesson of the contract billing).
        env = self.env(user=SUPERUSER_ID, su=True, context=dict(clean_context(self.env.context), lang=lang,
                                                                 mail_create_nosubscribe=True))
        label = env._("Book of %(count)s child care tickets, %(year)s: %(student)s", count=rate.tickets,
                      year=year.name, student=student.name)
        eligible = rate._school_eligible(rate.amount, rate.tickets)
        lines = []
        for price, care in ((eligible, True), (rate.amount - eligible, False)):
            if not float_is_zero(price, precision_rounding=school.company_id.currency_id.rounding):
                lines.append(Command.create({
                    "name": label if care or not eligible else env._("%s (not child care)", label),
                    "quantity": 1, "price_unit": price, "tax_ids": [Command.clear()], "school_rl24": care,
                    "school_childcare_student_id": student.id}))
        move = env["account.move"].with_company(school.company_id).create({
            "move_type": "out_invoice", "partner_id": partner.id, "invoice_user_id": False,
            "invoice_date": today, "invoice_origin": env._("Child care tickets"), "invoice_line_ids": lines})
        move.action_post()
        book = self.with_env(env).create({
            "student_id": student.id, "school_id": school.id, "year_id": year.id, "payer_id": partner.id,
            "tickets": rate.tickets, "price": rate.amount, "invoice_id": move.id, "state": "to_pay"})
        # The portal shows a « PROFORMA » until the official PDF exists (lesson of the admission fee).
        self.env.ref("bf_school_childcare_billing.ir_cron_childcare_ticket_pdf").sudo()._trigger()
        return book.with_env(self.env)

    def _school_paid(self):
        for book in self.sudo().filtered(lambda b: b.state == "to_pay"):
            if book.invoice_id.payment_state in ("paid", "in_payment"):
                book.state = "paid"

    def _school_unpaid(self):
        """The payment was undone: the book is to pay again. The periods it already paid stay paid by it:
        its invoice is due again, and no ticket pays anything more until it is paid."""
        self.sudo().filtered(lambda b: b.state == "paid").write({"state": "to_pay"})

    @api.model
    def _cron_invoice_pdf(self, limit=50):
        """The official PDF of recent invoices of books of tickets. Sends nothing."""
        since = fields.Datetime.subtract(fields.Datetime.now(), days=30)
        moves = self.sudo().search([("create_date", ">=", since), ("invoice_id.state", "=", "posted")]).invoice_id
        for move in moves.filtered(lambda m: not m.invoice_pdf_report_id)[:limit]:
            try:
                with self.env.cr.savepoint():
                    self.env["account.move.send"].sudo()._generate_and_send_invoices(move, sending_methods=[])
            except Exception:  # noqa: BLE001
                _logger.warning("Ticket book invoice %s: official PDF not generated", move.name, exc_info=True)

    def action_cancel(self):
        """A book not paid is cancelled with its invoice."""
        self.check_access("write")
        for book in self.sudo():
            if book.state == "to_pay" and book._school_credited():
                book.state = "cancelled"   # its invoice was credited in the accounting: nothing more to do
                continue
            if book.state != "to_pay" or book.invoice_id.payment_state not in ("not_paid",):
                raise UserError(_("Only a book not paid at all is cancelled; a payment is refunded in the accounting."))
            if book.invoice_id.state == "posted":
                book.invoice_id.button_draft()
            book.invoice_id.button_cancel()
            book.state = "cancelled"
        return True
