from datetime import timedelta

from odoo import _, api, fields, models

# Days before the due date when the family gets the reminder.
REMINDER_DAYS = 7
from odoo.exceptions import AccessError, UserError, ValidationError


class SchoolDeviceLoan(models.Model):
    """A Blue Fox OS computer lent to a student.

    Recording the loan is what opens the machine to the student: their
    directory user name is written on the machine (bf_policy's borrowers), and
    the machine picks it up at its next policy sync. Returning it, or marking
    it lost, takes the name off: back on the shelf, the machine opens only to
    its profile's groups (IT staff).
    """

    _name = "bf.school.device.loan"
    _description = "Computer lent to a student"
    _inherit = ["mail.thread"]
    _order = "state, date_due, id desc"
    _rec_name = "machine_id"

    school_id = fields.Many2one("bf.school", required=True, ondelete="restrict", index=True,
                                default=lambda s: s.env["bf.school"].search([], limit=1))
    company_id = fields.Many2one(related="school_id.company_id", store=True)
    machine_id = fields.Many2one(
        "bf.policy.machine", "Computer", required=True, ondelete="restrict", index=True,
        # The company is left to the record rule: a domain through org_id would need
        # read access on bf.policy.org, which the school office does not have (the
        # field's dropdown failed in the browser, "Oups").
        domain="[('seat_kind', '=', 'loan')]",
        tracking=True)
    student_id = fields.Many2one(
        "res.partner", required=True, ondelete="restrict", index=True, tracking=True,
        domain=[("is_student", "=", True)])
    account_id = fields.Many2one(
        "bf.school.account", compute="_compute_account_id", store=True, index=True)
    date_out = fields.Date("Lent on", required=True, default=fields.Date.context_today,
                           tracking=True)
    date_due = fields.Date("Due back", required=True, tracking=True,
                           default=lambda s: s._default_date_due())
    date_returned = fields.Date("Returned on", readonly=True, tracking=True)
    state = fields.Selection(
        [("out", "Lent"), ("returned", "Returned"), ("lost", "Lost or damaged")],
        default="out", required=True, readonly=True, tracking=True)
    is_overdue = fields.Boolean(compute="_compute_is_overdue", search="_search_is_overdue")
    condition_out = fields.Text("Condition when lent", help="Charger, case, visible damage.")
    condition_in = fields.Text("Condition when returned")
    # Each notice goes once: the date says it went, and to nobody twice.
    notice_out_on = fields.Datetime("Family told of the loan", readonly=True, copy=False)
    notice_reminder_on = fields.Datetime("Reminder sent", readonly=True, copy=False)
    notice_overdue_on = fields.Datetime("Overdue notice sent", readonly=True, copy=False)

    @api.model
    def _default_date_due(self):
        school = self.env["bf.school"].browse(self.env.context.get("default_school_id")) \
            or self.env["bf.school"].search([], limit=1)
        return school.current_year_id.date_end or fields.Date.context_today(self)

    @api.depends("student_id")
    def _compute_account_id(self):
        Account = self.env["bf.school.account"]
        for loan in self:
            loan.account_id = Account.search([("student_id", "=", loan.student_id.id)], limit=1)

    def _compute_is_overdue(self):
        today = fields.Date.context_today(self)
        for loan in self:
            loan.is_overdue = loan.state == "out" and loan.date_due < today

    def _search_is_overdue(self, operator, value):
        if operator not in ("=", "!=") or not isinstance(value, bool):
            raise UserError(_("Unsupported search."))
        domain = [("state", "=", "out"), ("date_due", "<", fields.Date.context_today(self))]
        return domain if (operator == "=") == value else ["!"] + domain

    def init(self):
        # One open loan per computer: two students must never both be borrowers
        # of the same machine because two tabs recorded the loan at once.
        self.env.cr.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS bf_school_device_loan_one_open
            ON bf_school_device_loan (machine_id) WHERE state = 'out'
        """)

    @api.constrains("date_out", "date_due")
    def _check_dates(self):
        for loan in self:
            if loan.date_due < loan.date_out:
                raise ValidationError(_("The computer cannot be due back before it is lent."))

    @api.constrains("machine_id", "school_id")
    def _check_machine(self):
        for loan in self:
            machine = loan.machine_id.sudo()
            if machine.seat_kind != "loan":
                raise ValidationError(_(
                    "%s is not a computer for lending: enrol it under a loan profile.",
                    machine.hostname))
            if machine.org_id.company_id != loan.company_id:
                raise ValidationError(_("%s belongs to another organisation.", machine.hostname))
            if not machine.active:
                raise ValidationError(_("%s is revoked.", machine.hostname))

    @api.model_create_multi
    def create(self, vals_list):
        # The partial unique index stays the guard against two tabs at once; this
        # check only turns the usual case into a sentence instead of an SQL error.
        busy = self.sudo().search([("state", "=", "out"), ("machine_id", "in", [
            v.get("machine_id") for v in vals_list if v.get("machine_id")])])
        if busy:
            # The student's name only for a loan this user may see: no name from
            # another company through a guessed machine id.
            if busy[0].company_id in self.env.companies:
                raise UserError(_("%(machine)s is already lent to %(student)s.",
                                  machine=busy[0].machine_id.hostname,
                                  student=busy[0].student_id.name))
            raise UserError(_("This computer is already lent."))
        loans = super().create(vals_list)
        for loan in loans:
            account = loan.account_id
            if not account or account.state != "active":
                raise UserError(_(
                    "%s has no active directory account: create it from the school "
                    "(Student accounts) before lending a computer.", loan.student_id.name))
            if account.school_id != loan.school_id:
                raise UserError(_("%s's account belongs to another school.",
                                  loan.student_id.name))
        loans._apply_borrowers()
        loans._notify_families("bf_school_device.mail_template_loan_out", "notice_out_on")
        return loans

    # 🔴 readonly= only guards the screen. The state, the return date and the notice
    # stamps move through the buttons and the daily job, never by a plain write
    # (found in QA, 2026-09-27): a loan "returned" by RPC skipped nothing, but
    # a stamp written by hand silenced a notice, and a closed loan's dates could be
    # rewritten after the fact.
    _SYSTEM_FIELDS = frozenset({"state", "date_returned", "notice_out_on",
                                "notice_reminder_on", "notice_overdue_on"})
    # What a closed loan still accepts: the condition noted when it came back.
    _CLOSED_WRITABLE = frozenset({"condition_in"})

    def write(self, vals):
        if not self.env.su:
            if self._SYSTEM_FIELDS & set(vals):
                raise AccessError(_("The status, the return date and the notices of a loan "
                                    "move through its buttons."))
            business = {k for k in vals if k in self._fields and not k.startswith(
                ("message_", "activity_", "website_message"))}
            if business - self._CLOSED_WRITABLE and self.filtered(lambda l: l.state != "out"):
                raise UserError(_("A closed loan is a record: only the condition when "
                                  "returned can still be noted."))
        if "student_id" in vals and any(l.student_id.id != vals["student_id"] for l in self):
            # Another student is another loan: its account, its school and its
            # family's notice are checked and sent at creation.
            raise UserError(_("A loan cannot change student: return the computer and "
                              "lend it again."))
        if "machine_id" in vals and self.filtered(lambda l: l.state != "out"):
            raise UserError(_("A closed loan cannot change computer or student."))
        old_machines = self.machine_id
        res = super().write(vals)
        if {"machine_id", "state"} & set(vals):
            # Whatever path closed the loan (button, import, RPC), the machine follows.
            (old_machines - self.machine_id)._school_clear_borrowers()
            self.filtered(lambda l: l.state != "out").machine_id._school_clear_borrowers()
            self._apply_borrowers()
        return res

    def unlink(self):
        if self.filtered(lambda l: l.state == "out"):
            raise UserError(_("Return the computer (or mark it lost) instead of deleting "
                              "the loan."))
        return super().unlink()

    def _apply_borrowers(self):
        for loan in self.filtered(lambda l: l.state == "out"):
            loan.machine_id.sudo().seat_allowed_users = (
                loan.account_id.username if loan.account_id.state == "active" else False)

    @api.model
    def _refresh_borrowers(self):
        """A student who left keeps the computer until the office gets it back, but not
        the right to log in on it: the machine drops them at its next policy sync."""
        self.sudo().search([("state", "=", "out")])._apply_borrowers()

    def action_return(self):
        self.check_access("write")  # the write below is in sudo
        for loan in self.filtered(lambda l: l.state == "out"):
            loan.sudo().write({"state": "returned", "date_returned": fields.Date.context_today(self)})
            loan.machine_id._school_clear_borrowers()

    def action_mark_lost(self):
        self.check_access("write")  # the write below is in sudo
        for loan in self.filtered(lambda l: l.state == "out"):
            loan.sudo().write({"state": "lost", "date_returned": fields.Date.context_today(self)})
            loan.machine_id._school_clear_borrowers()

    # ------------------------------------------------------------ notices
    def _notify_families(self, template_xmlid, stamp_field):
        """One email per adult who receives the school's notices, in their language.

        Stamped even when nobody has an email address, so the daily job does not
        retry forever; the loan's chatter says who was written to."""
        template = self.env.ref(template_xmlid, raise_if_not_found=False)
        now = fields.Datetime.now()
        for loan in self.sudo():
            if loan[stamp_field]:
                continue
            loan[stamp_field] = now
            if not template:
                continue
            adults = loan.student_id.student_guardian_link_ids.filtered(
                "receives_notices").guardian_id.filtered("email")
            for adult in adults:
                lang = adult.lang or loan.company_id.partner_id.lang or "fr_CA"
                template.with_context(lang=lang, school_lang=lang).send_mail(
                    loan.id, force_send=False,
                    email_layout_xmlid=self.env["bf.school"]._school_mail_layout(),
                    email_values={"recipient_ids": [(6, 0, adult.ids)], "email_to": False})
            loan._message_log(body=_("%(notice)s: %(who)s", notice=template.name,
                                     who=", ".join(adults.mapped("name")) or _("nobody (no adult receives notices with an email)")))
        self.env.ref("mail.ir_cron_mail_scheduler_action").sudo()._trigger()

    @api.model
    def _cron_loan_notices(self):
        today = fields.Date.context_today(self)
        Loan = self.sudo()
        Loan.search([("state", "=", "out"), ("notice_reminder_on", "=", False),
                     ("date_due", ">=", today),
                     ("date_due", "<=", today + timedelta(days=REMINDER_DAYS))]
                    )._notify_families("bf_school_device.mail_template_loan_reminder",
                                       "notice_reminder_on")
        Loan.search([("state", "=", "out"), ("notice_overdue_on", "=", False),
                     ("date_due", "<", today)]
                    )._notify_families("bf_school_device.mail_template_loan_overdue",
                                       "notice_overdue_on")

    # ------------------------------------------------------------- portal
    @api.model
    def _school_for_guardian(self, partner):
        """Open loans of the children this adult is linked to, for the family portal."""
        students = partner.sudo()._school_portal_links().student_id
        return self.sudo().search([("student_id", "in", students.ids), ("state", "=", "out")])


class PolicyMachine(models.Model):
    _inherit = "bf.policy.machine"

    # groups= : without it, reading a machine as a system administrator who is not
    # school staff (the Policy menu, bf_policy's own tests) fails on this field.
    school_loan_ids = fields.One2many("bf.school.device.loan", "machine_id", "Loans",
                                      groups="bf_school_core.group_school_user")

    def _school_clear_borrowers(self):
        """Back on the shelf: only the profile's groups may log in."""
        for machine in self.sudo():
            if machine.seat_profile_id and not machine.school_loan_ids.filtered(
                    lambda l: l.state == "out"):
                machine.seat_allowed_users = False
