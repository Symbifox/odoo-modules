import logging
from collections import defaultdict
from datetime import timedelta

from markupsafe import Markup

from odoo import SUPERUSER_ID, Command, _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import clean_context, float_is_zero, format_date

from odoo.addons.bf_school_billing.models.fee import split

from .charge import month_bounds

_logger = logging.getLogger(__name__)


class ChildcareBillingRun(models.Model):
    """The billing of a school's child care for one month: its charges, then one invoice per payer."""

    _name = "bf.school.childcare.billing.run"
    _description = "Child care billing of a month"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "month desc, school_id"

    school_id = fields.Many2one("bf.school", "School", required=True, ondelete="restrict", index=True)
    company_id = fields.Many2one(related="school_id.company_id", store=True, index=True)
    currency_id = fields.Many2one(related="company_id.currency_id")
    month = fields.Date(required=True, help="Any day of the month billed.")
    month_label = fields.Char("Month billed", compute="_compute_display_name")
    state = fields.Selection([("draft", "To bill"), ("billed", "Billed")], default="draft", required=True,
                             readonly=True, tracking=True)
    billed_on = fields.Datetime(readonly=True, copy=False)
    charge_ids = fields.One2many("bf.school.childcare.charge", compute="_compute_charges", string="Charges")
    charge_count = fields.Integer(compute="_compute_charges")
    amount_open = fields.Monetary("To bill", compute="_compute_charges")
    amount_billed = fields.Monetary("Billed", compute="_compute_charges")
    invoice_count = fields.Integer("Invoices", compute="_compute_charges")

    _sql_constraints = [("school_month_unique", "UNIQUE(school_id, month)", "A school bills a month once.")]

    @api.depends("school_id", "month")
    @api.depends_context("lang")
    def _compute_display_name(self):
        for run in self:
            run.month_label = run.month and format_date(self.env, run.month, date_format="MMMM yyyy")
            run.display_name = " · ".join(filter(None, [run.school_id.name, run.month_label]))

    @api.depends("school_id", "month")
    def _compute_charges(self):
        Charge = self.env["bf.school.childcare.charge"].sudo()
        for run in self:
            charges = Charge.search([("school_id", "=", run.school_id.id), ("month", "=", run.month)]) \
                if run.school_id and run.month else Charge
            run.charge_ids = charges.with_env(self.env)
            run.charge_count = len(charges)
            run.amount_open = sum(charges.filtered(lambda c: c.state == "open").mapped("amount"))
            run.amount_billed = sum(charges.filtered(lambda c: c.state == "billed").mapped("amount"))
            run.invoice_count = len(charges.invoice_ids)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not self.env.su and (vals.get("state") not in (None, "draft") or vals.get("billed_on")):
                raise UserError(_("A billing starts to bill."))
            if vals.get("month"):
                vals["month"] = fields.Date.to_date(vals["month"]).replace(day=1)
            # 🔴 Written: Odoo adds the caller's `default_*` and own ir.default after this check.
            vals.setdefault("state", "draft")
            vals.setdefault("billed_on", False)
        # No "created" note: Odoo's own agrees in gender with nothing ("Facturation … créé").
        return super(ChildcareBillingRun, self.with_context(mail_create_nolog=True)).create(vals_list)

    def write(self, vals):
        if not self.env.su and {"school_id", "month", "state", "billed_on"} & set(vals):
            raise UserError(_("A billing keeps its school and its month."))
        return super().write(vals)

    def _school_check_month_ended(self):
        for run in self:
            _start, end = month_bounds(run.month)
            if end >= run.school_id._calendar_today():
                raise UserError(_("%s has not ended: a month is billed once it is over.",
                                  format_date(self.env, run.month, date_format="MMMM yyyy")))

    # --- Buttons ----------------------------------------------------------------------------

    def action_compute(self):
        """Compute the charges of the month again, without billing them."""
        self.check_access("write")
        self._school_check_month_ended()
        for run in self.sudo():
            run._school_lock()
            run._school_note_missing(self.env["bf.school.childcare.charge"]._school_compute(run.school_id, run.month))
        return True

    def action_bill(self):
        """Compute the charges, then invoice what is still to bill, one invoice per payer."""
        self.check_access("write")
        self._school_check_month_ended()
        for run in self.sudo():
            run._school_bill()
        return True

    def action_view_charges(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id("bf_school_childcare_billing.bf_school_childcare_charge_action")
        action["domain"] = [("school_id", "=", self.school_id.id), ("month", "=", self.month)]
        action["context"] = {}
        return action

    def _school_note_missing(self, missing):
        if not missing:
            return
        kinds = dict(self.env["bf.school.childcare.rate"]._fields["kind"]._description_selection(self.env))
        kinds["regular"] = _("Monthly tier for %s periods a week")
        labels = []
        for kind, what in missing:
            if kind == "regular":
                labels.append(kinds["regular"] % what)
            else:
                labels.append(" · ".join(filter(None, [kinds.get(kind, kind), what])))
        self.message_post(body=Markup("%s<ul>%s</ul>") % (
            _("Nothing was charged where no price is set:"),
            Markup("").join(Markup("<li>%s</li>") % label for label in labels)),
            message_type="notification", subtype_xmlid="mail.mt_note")

    # --- Invoices -------------------------------------------------------------------------------

    def _school_payers(self, student, year):
        """[(adult, weight)] who pay for the student's child care this year.

        The family shares of the student's contract (shared custody), else the adults ticked « Pays »
        in equal shares, else the first adult with parental authority who signs.
        """
        contracts = self.env["bf.school.contract"].sudo().search(
            [("student_id", "=", student.id), ("year_id", "=", year.id), ("state", "!=", "draft")],
            order="id desc")
        for contract in contracts:
            families = contract.share_ids.filtered(lambda s: s.kind == "family" and s.percent > 0)
            if families:
                return [(share.partner_id, share.percent) for share in families]
        links = student.sudo().student_guardian_link_ids.sorted(lambda l: (l.sequence, l.id))
        payers = links.filtered(lambda l: l.is_payer and l.guardian_id.active).guardian_id
        if payers:
            return [(payer, 1.0) for payer in payers]
        signers = links.filtered(lambda l: l.has_parental_authority and l.can_sign and l.guardian_id.active)
        return [(signers[0].guardian_id, 1.0)] if signers else []

    def _school_lock(self):
        """Serialize two billings of the same month (the button and the cron): the second one is replayed
        by Odoo and sees what the first one did (see bf_school_meal: an UPDATE, not a row lock)."""
        self.env.cr.execute("UPDATE bf_school_childcare_billing_run SET write_date = now() at time zone 'UTC' "
                            "WHERE id IN %s", [tuple(self.ids)])

    def _school_office(self):
        """Who is told what the billing could not do: the Administration person who billed, else the one
        who created the billing, else the school's first Administration user (🔴 the cron bills as the
        system: its notices fell on the child-care manager, who can neither tick « Pays » nor bill),
        else the child-care manager."""
        self.ensure_one()
        for user in (self.env.user, self.create_uid):
            if user and user.id != SUPERUSER_ID and user.active and not user.share \
                    and user.has_group("bf_school_core.group_school_manager"):
                return user
        office = self.env["res.users"].sudo().search([
            ("groups_id", "in", self.env.ref("bf_school_core.group_school_manager").id), ("share", "=", False),
            ("id", "!=", SUPERUSER_ID), ("company_ids", "in", self.company_id.id)], order="id", limit=1)
        return office or self.school_id._childcare_responsible()

    def _school_tell(self, summary, body):
        """A note on the billing, and an activity for the office unless the same one is still open."""
        self.message_post(body=body, message_type="notification", subtype_xmlid="mail.mt_note")
        office = self._school_office()
        if office and not self.activity_ids.filtered(lambda a: a.summary == summary and a.user_id == office):
            self.activity_schedule("mail.mail_activity_data_todo", user_id=office.id, summary=summary, note=body)

    def _school_run_of(self, month):
        Run = self.sudo().with_context(clean_context(self.env.context))
        return Run.search([("school_id", "=", self.school_id.id), ("month", "=", month)]) or Run.create(
            {"school_id": self.school_id.id, "month": month})

    def _school_bill(self):
        self.ensure_one()
        self._school_lock()
        Charge = self.env["bf.school.childcare.charge"].sudo()
        self._school_note_missing(Charge._school_compute(self.school_id, self.month))
        _start, end = month_bounds(self.month)
        charges = Charge.search([("school_id", "=", self.school_id.id), ("state", "=", "open"),
                                 ("date", "<=", end)], order="date, id")
        # 🔴 One month per invoice: a charge left open from an earlier month (nobody paid then) is billed
        # under the billing of ITS month, whose year the Relevé 24 counts.
        by_month = defaultdict(lambda: Charge.browse())
        for charge in charges:
            by_month[charge.month] |= charge
        moves = self.env["account.move"]
        for month in sorted(by_month):
            run = self if month == self.month else self._school_run_of(month)
            moves |= run._school_invoice_charges(by_month[month])
        self.write({"state": "billed", "billed_on": fields.Datetime.now()})
        return moves

    def _school_invoice_charges(self, charges):
        """Invoice these open charges of this billing's month, one invoice per payer."""
        self.ensure_one()
        free = charges.filtered(lambda c: float_is_zero(c.amount, precision_rounding=c.currency_id.rounding))
        free.write({"state": "billed"})
        by_payer, no_payer = defaultdict(list), self.env["res.partner"]
        for (student, year), group in self._school_group(charges - free).items():
            payers = self._school_payers(student, year)
            if not payers:
                no_payer |= student
                continue
            weights = [weight for _payer, weight in payers]
            for charge in group:
                amounts, eligibles = split(charge.amount, weights), split(charge.eligible_amount, weights)
                for (payer, _weight), amount, eligible in zip(payers, amounts, eligibles):
                    by_payer[payer].append((charge, amount, min(eligible, amount)))
        moves = self.env["account.move"]
        for payer, parts in by_payer.items():
            moves |= self._school_invoice(payer, parts)
        if no_payer:
            self._school_tell(_("Child care not billed: no payer"), _(
                "Nobody pays for %s: tick « Pays » on an adult of the family, then bill again.",
                ", ".join(no_payer.mapped("name"))))
        if moves:
            self.message_post(body=_("Billed: %s.", ", ".join("%s (%s)" % (m.partner_id.name, m.name) for m in moves)),
                              message_type="notification", subtype_xmlid="mail.mt_note")
            self.env.ref("bf_school_childcare_billing.ir_cron_childcare_billing_send").sudo()._trigger()
        if self.state != "billed":
            self.write({"state": "billed", "billed_on": fields.Datetime.now()})
        return moves

    @staticmethod
    def _school_group(charges):
        groups = defaultdict(lambda: charges.browse())
        for charge in charges:
            groups[(charge.student_id, charge.year_id)] |= charge
        return groups

    def _school_invoice(self, payer, parts):
        """One invoice for `payer`: [(charge, amount, eligible)], each line split in its child-care part
        and the rest."""
        self.ensure_one()
        lang = payer.lang or self.company_id.partner_id.lang or "fr_CA"
        # 🔴 The superuser, not sudo(): the clicker's own ir.default went into the invoices of the
        # contract billing (lesson of bf_school_billing). Nobody follows it but its payer.
        env = self.env(user=SUPERUSER_ID, su=True, context=dict(clean_context(self.env.context), lang=lang,
                                                                 mail_create_nosubscribe=True))
        lines = []
        for charge, amount, eligible in sorted(parts, key=lambda p: (p[0].student_id.name, p[0].date, p[0].id)):
            label = "%s : %s" % (charge.student_id.name, charge._school_label(env))
            rest = amount - eligible
            for price, care in ((eligible, True), (rest, False)):
                if float_is_zero(price, precision_rounding=charge.currency_id.rounding):
                    continue
                lines.append(Command.create({
                    "name": label if care or not eligible else env._("%s (not child care)", label),
                    "quantity": 1, "price_unit": price, "tax_ids": [Command.clear()],
                    "school_rl24": care, "school_childcare_student_id": charge.student_id.id}))
        if not lines:
            return env["account.move"]
        move = env["account.move"].with_company(self.company_id).create({
            "move_type": "out_invoice", "partner_id": payer.id, "invoice_user_id": False,
            "invoice_date": self.school_id._calendar_today(),
            "invoice_origin": env._("Child care, %s", format_date(env, self.month, date_format="MMMM yyyy")),
            "school_childcare_run_id": self.id, "invoice_line_ids": lines,
        })
        move.action_post()
        charges = self.env["bf.school.childcare.charge"].sudo().browse({charge.id for charge, _a, _e in parts})
        for charge in charges:
            charge.write({"state": "billed", "invoice_ids": [Command.link(move.id)]})
        return move

    # --- Cron ---------------------------------------------------------------------------------------

    @api.model
    def _cron_bill_last_month(self):
        """On the first of the month: the child care of last month, at every school that has some."""
        Run = self.sudo()
        schools = (self.env["bf.school.childcare.rate"].sudo().search([]).school_id
                   | self.env["bf.school.childcare.tier"].sudo().search([]).school_id)
        for school in schools:
            today = school._calendar_today()
            month = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
            run = Run.search([("school_id", "=", school.id), ("month", "=", month)]) or Run.create(
                {"school_id": school.id, "month": month})
            try:
                with self.env.cr.savepoint():
                    run._school_bill()
            except Exception:  # noqa: BLE001
                _logger.warning("Child care of %s not billed", run.display_name, exc_info=True)
                run.message_post(body=_("The billing of the month failed: bill it with the button."),
                                 message_type="notification", subtype_xmlid="mail.mt_note")

    @api.model
    def _cron_send_invoices(self, limit=50):
        """Send the new child-care invoices to their payers, with their PDF, once (as the contract
        billing: a failure is tried again, a payer without an email address is told to the school)."""
        since = fields.Datetime.subtract(fields.Datetime.now(), days=30)
        moves = self.env["account.move"].sudo().search([
            ("school_childcare_run_id", "!=", False), ("move_type", "=", "out_invoice"), ("state", "=", "posted"),
            ("school_billing_sent", "=", False), ("create_date", ">=", since)], limit=limit)
        for move in moves:
            run = move.school_childcare_run_id
            if move.message_ids.filtered(lambda m: m.message_type == "comment" and m.attachment_ids
                                         and move.partner_id in m.partner_ids):
                move.school_billing_sent = True
                continue
            if not move.partner_id.email:
                move.school_billing_sent = True
                run._school_tell(_("Invoice not sent: no email address"), _(
                    "%(payer)s has no email address: %(move)s was not sent. Give it to them another way.",
                    payer=move.partner_id.name, move=move.name))
                continue
            try:
                with self.env.cr.savepoint():
                    self.env["account.move.send"].sudo()._generate_and_send_invoices(move, sending_methods=["email"])
                    move.school_billing_sent = True
            except Exception:  # noqa: BLE001
                _logger.warning("Child care invoice %s not sent, tried again at the next run", move.name,
                                exc_info=True)
