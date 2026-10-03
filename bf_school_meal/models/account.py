import logging
import math
from datetime import date

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import format_amount

_logger = logging.getLogger(__name__)

#: Meals served to students at school are exempt (GST: Excise Tax Act, Sched. V,
#: Part III, s. 12; QST: same treatment). The lines carry no tax; the school's
#: accountant confirms before going live.
NO_TAX = [(6, 0, [])]


class MealAccount(models.Model):
    """One per adult who pays, per company: the prepaid balance, or the monthly invoice."""

    _name = "bf.school.meal.account"
    _description = "Meal account"
    _rec_name = "partner_id"

    partner_id = fields.Many2one("res.partner", "Pays", required=True, ondelete="restrict", index=True)
    company_id = fields.Many2one("res.company", required=True, index=True)
    currency_id = fields.Many2one(related="company_id.currency_id")
    mode = fields.Selection([("prepaid", "Prepaid balance"), ("monthly", "Monthly invoice")],
                            required=True, default="prepaid")
    entry_ids = fields.One2many("bf.school.meal.entry", "account_id", "Movements")
    topup_ids = fields.One2many("bf.school.meal.topup", "account_id", "Top-ups")
    order_ids = fields.One2many("bf.school.meal.order", "account_id", "Orders")
    balance = fields.Monetary(compute="_compute_balance")

    _sql_constraints = [("one_per_payer", "UNIQUE(partner_id, company_id)", "An adult has one meal account.")]

    @api.depends("entry_ids.amount")
    def _compute_balance(self):
        for account in self:
            account.balance = sum(account.sudo().entry_ids.mapped("amount"))

    @api.model
    def _school_get(self, partner, company, school=None):
        account = self.sudo().search([("partner_id", "=", partner.id), ("company_id", "=", company.id)])
        if not account:
            mode = "monthly" if school and school.meal_payment_modes == "monthly" else "prepaid"
            account = self.sudo().create({"partner_id": partner.id, "company_id": company.id, "mode": mode})
        return account

    def _school_lock(self):
        """Serialize the orders of one account.

        🔴 A row lock alone does not do it: under REPEATABLE READ, the transaction that waited
        for the lock still reads the balance and the orders of its old snapshot. UPDATING the
        account row does: the second transaction fails to serialize, Odoo replays it, and the
        replay sees the first order (its debit, or its twin).
        """
        self.env.cr.execute("UPDATE bf_school_meal_account SET write_date = now() at time zone 'UTC' "
                            "WHERE id IN %s", [tuple(self.ids)])

    def _school_debit(self, order):
        self.ensure_one()
        # 🔴 Two orders sent at once must not both spend the last $6 (see _school_lock).
        self._school_lock()
        self.invalidate_recordset(["balance"])
        if self.balance < order.price:
            raise UserError(_("The meal balance is too low (%(balance)s): top it up first.",
                              balance=self.currency_id.format(self.balance)))
        self.env["bf.school.meal.entry"].sudo().create({
            "account_id": self.id, "amount": -order.price, "kind": "order", "order_id": order.id})

    def _school_credit(self, order):
        self.ensure_one()
        self.env["bf.school.meal.entry"].sudo().create({
            "account_id": self.id, "amount": order.price, "kind": "refund", "order_id": order.id})

    def _school_set_mode(self, mode, school):
        """The family changes how it pays, when the school lets it choose."""
        self.ensure_one()
        if school.meal_payment_modes != "choice":
            raise UserError(_("The school sets how meals are paid."))
        if mode == self.mode:
            return True
        if mode == "monthly" and self.balance:
            raise UserError(_("Use up or have the office refund the prepaid balance before changing."))
        self.sudo().mode = mode
        return True

    def _school_topup(self, amount):
        """An invoice for the top-up: paid online or recorded by the office, the balance follows."""
        self.ensure_one()
        if self.mode != "prepaid":
            raise UserError(_("This account is billed monthly: there is nothing to top up."))
        if not math.isfinite(amount) or amount < 5 or amount > 500:
            raise UserError(_("A top-up is between 5 and 500."))
        move = self.env["account.move"].sudo().with_company(self.company_id).create({
            "move_type": "out_invoice", "partner_id": self.partner_id.id,
            # 🔴 The default salesperson is the current user: the parent on the portal, and
            # the invoice email would leave in their name. The school sends it.
            "invoice_user_id": False,
            "invoice_origin": _("Meal account"),
            "invoice_line_ids": [(0, 0, {"name": _("Meal account top-up"), "quantity": 1,
                                         "price_unit": amount, "tax_ids": NO_TAX})],
        })
        move.action_post()
        topup = self.env["bf.school.meal.topup"].sudo().create(
            {"account_id": self.id, "amount": amount, "invoice_id": move.id})
        # Same lesson as the admission fee: the official PDF comes from a triggered cron.
        self.env.ref("bf_school_meal.ir_cron_school_meal_invoice_pdf").sudo()._trigger()
        return topup

    # --- Monthly invoice -------------------------------------------------------------

    def _school_invoice_month(self, month):
        """One invoice per account for the meals ordered in `month` (its first day)."""
        start, end = month, month + relativedelta(months=1, days=-1)
        moves = self.env["account.move"]
        for account in self.sudo():
            orders = account.order_ids.filtered(
                lambda o: o.mode == "monthly" and o.state == "ordered" and not o.invoice_id
                and start <= o.date <= end and o.price)
            if not orders:
                continue
            lines = [(0, 0, {
                "name": "%s, %s : %s" % (fields.Date.to_string(o.date), o.student_id.name, o.item_id.name),
                "quantity": 1, "price_unit": o.price, "tax_ids": NO_TAX,
            }) for o in orders.sorted(lambda o: (o.date, o.student_id.name))]
            # The cron has no language: the invoice speaks the payer's.
            env = account.with_context(lang=account.partner_id.lang or self.env.lang).env
            move = self.env["account.move"].with_company(account.company_id).create({
                "move_type": "out_invoice", "partner_id": account.partner_id.id,
                # 🔴 Created by the cron: without this, OdooBot was the salesperson and the email
                # left from "OdooBot" <odoobot@…> (found in QA, 2026-09-27).
                "invoice_user_id": False,
                "invoice_origin": env._("Meals %s", month.strftime("%Y-%m")), "invoice_line_ids": lines})
            move.action_post()
            orders.write({"invoice_id": move.id})
            moves |= move
        return moves

    @api.model
    def _cron_monthly_invoices(self, today=None):
        """On the first days of a month: last month's meals, invoiced and sent to the adult who pays."""
        today = today or fields.Date.context_today(self)
        month = date(today.year, today.month, 1) - relativedelta(months=1)
        accounts = self.sudo().search([("mode", "=", "monthly")])
        # Accounts that changed to prepaid still owe last month's monthly meals.
        accounts |= self.env["bf.school.meal.order"].sudo().search([
            ("mode", "=", "monthly"), ("state", "=", "ordered"), ("invoice_id", "=", False),
            ("date", ">=", month), ("date", "<", month + relativedelta(months=1))]).account_id
        moves = accounts._school_invoice_month(month)
        for move in moves:
            try:
                with self.env.cr.savepoint():
                    self.env["account.move.send"].sudo()._generate_and_send_invoices(
                        move, sending_methods=["email"])
            except Exception:  # noqa: BLE001
                _logger.warning("Meal invoice %s not sent", move.name, exc_info=True)
        return moves

    @api.model
    def _cron_invoice_pdf(self, limit=50):
        """The official PDF of recent top-up invoices. Sends nothing."""
        since = fields.Datetime.subtract(fields.Datetime.now(), days=30)
        moves = self.env["bf.school.meal.topup"].sudo().search(
            [("create_date", ">=", since), ("invoice_id.state", "=", "posted")]).invoice_id
        for move in moves.filtered(lambda m: not m.invoice_pdf_report_id)[:limit]:
            try:
                with self.env.cr.savepoint():
                    self.env["account.move.send"].sudo()._generate_and_send_invoices(move, sending_methods=[])
            except Exception:  # noqa: BLE001
                _logger.warning("Meal top-up invoice %s: official PDF not generated", move.name, exc_info=True)


class MealEntry(models.Model):
    """A movement of the prepaid balance. Never edited: a mistake is corrected by another movement."""

    _name = "bf.school.meal.entry"
    _description = "Meal account movement"
    _order = "create_date desc, id desc"

    account_id = fields.Many2one("bf.school.meal.account", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="account_id.company_id", store=True)
    currency_id = fields.Many2one(related="account_id.currency_id")
    amount = fields.Monetary(required=True)
    kind = fields.Selection([("topup", "Top-up"), ("order", "Meal"), ("refund", "Credit"),
                             ("adjustment", "Adjustment by the office")], required=True)
    order_id = fields.Many2one("bf.school.meal.order", "Meal", ondelete="restrict")
    topup_id = fields.Many2one("bf.school.meal.topup", "Top-up", ondelete="restrict")
    note = fields.Char()

    @api.depends("kind", "order_id", "topup_id", "note")
    @api.depends_context("lang")
    def _compute_display_name(self):
        kinds = dict(self._fields["kind"]._description_selection(self.env))
        for entry in self:
            entry.display_name = " · ".join(filter(None, [
                kinds.get(entry.kind), entry.order_id.display_name or entry.topup_id.display_name or entry.note]))

    def write(self, vals):
        raise UserError(_("A movement is not changed: record an adjustment."))

    def unlink(self):
        raise UserError(_("A movement is not deleted: record an adjustment."))

    @api.constrains("kind", "note")
    def _check_adjustment(self):
        for entry in self:
            if entry.kind == "adjustment" and not (entry.note or "").strip():
                raise ValidationError(_("An adjustment says why."))


class MealTopup(models.Model):
    _name = "bf.school.meal.topup"
    _description = "Meal account top-up"
    _order = "create_date desc"

    account_id = fields.Many2one("bf.school.meal.account", required=True, ondelete="cascade", index=True)
    currency_id = fields.Many2one(related="account_id.currency_id")
    amount = fields.Monetary(required=True)
    invoice_id = fields.Many2one("account.move", required=True, readonly=True)
    state = fields.Selection([("pending", "To pay"), ("paid", "Paid")], default="pending", required=True)

    @api.depends("create_date", "amount", "currency_id")
    @api.depends_context("lang")
    def _compute_display_name(self):
        # Not the invoice's number: the office reads the top-ups without Invoicing, which
        # opens account.move (adversarial review, 2026-10-03).
        for topup in self:
            tz = topup.account_id.company_id.partner_id.tz or "America/Toronto"
            day = topup.create_date and fields.Datetime.context_timestamp(topup.with_context(tz=tz), topup.create_date).date()
            topup.display_name = " · ".join(filter(None, [
                fields.Date.to_string(day), format_amount(self.env, topup.amount, topup.currency_id)]))

    def _school_paid(self):
        for topup in self.filtered(lambda t: t.state == "pending"):
            # 🔴 Credit what was paid, not what was asked: a partial credit note plus a small
            # payment also leaves the invoice "paid".
            invoice = topup.invoice_id
            paid = invoice.amount_total - invoice.amount_residual - sum(
                invoice.reversal_move_ids.filtered(lambda m: m.state == "posted").mapped("amount_total"))
            if paid <= 0:
                continue
            topup.state = "paid"
            self.env["bf.school.meal.entry"].sudo().create({
                "account_id": topup.account_id.id, "amount": min(paid, topup.amount), "kind": "topup",
                "topup_id": topup.id})

    def _school_unpaid(self):
        """The payment was undone (cancelled, unreconciled, reset to draft): the credit is taken back."""
        for topup in self.filtered(lambda t: t.state == "paid"):
            credited = sum(topup.account_id.entry_ids.filtered(lambda e: e.topup_id == topup).mapped("amount"))
            topup.state = "pending"
            if credited:
                self.env["bf.school.meal.entry"].sudo().create({
                    "account_id": topup.account_id.id, "amount": -credited, "kind": "adjustment",
                    "topup_id": topup.id, "note": _("Payment of the top-up undone")})


class AccountMove(models.Model):
    _inherit = "account.move"

    def _invoice_paid_hook(self):
        """Paid online (any provider) or recorded by the office: the balance is credited once."""
        result = super()._invoice_paid_hook()
        self.env["bf.school.meal.topup"].sudo().search([("invoice_id", "in", self.ids)])._school_paid()
        return result


class AccountPartialReconcile(models.Model):
    _inherit = "account.partial.reconcile"

    def unlink(self):
        """Every undone payment passes here (payment cancelled, unreconciled, invoice reset to draft)."""
        moves = (self.debit_move_id | self.credit_move_id).move_id
        result = super().unlink()
        topups = self.env["bf.school.meal.topup"].sudo().search(
            [("invoice_id", "in", moves.ids), ("state", "=", "paid")])
        topups.filtered(lambda t: t.invoice_id.payment_state not in ("paid", "in_payment"))._school_unpaid()
        return result
