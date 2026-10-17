from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from .bf_key_type import ELECTRONIC, KINDS, close_activities, schedule_activity

END_REASONS = [
    ("returned", "Returned"),
    ("lost", "Lost"),
    ("stolen", "Stolen"),
    ("destroyed", "Destroyed"),
]


class BfKeyAssignment(models.Model):
    """One hand-out: a copy, the person who holds it, from when to when.

    The person is an employee, or someone without an account (a contractor, a
    volunteer): then their name, phone and employer are noted, and erased after
    the retention period once the copy is back.
    """
    _name = "bf.key.assignment"
    _description = "Key hand-out"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "date_returned desc, date_issued desc, id desc"
    # The holder handles the reminders on their own hand-outs without being able to edit them.
    _mail_post_access = "read"

    copy_id = fields.Many2one("bf.key.copy", string="Copy", required=True, index=True,
                              ondelete="restrict", tracking=True)
    type_id = fields.Many2one("bf.key.type", related="copy_id.type_id", store=True, index=True)
    kind = fields.Selection(related="copy_id.kind", store=True)
    area_id = fields.Many2one("bf.key.area", related="type_id.area_id", store=True, index=True)
    employee_id = fields.Many2one("hr.employee", string="Employee", index=True, tracking=True,
                                  ondelete="restrict")
    holder_user_id = fields.Many2one(
        "res.users", string="Holder's account", compute="_compute_holder_user_id", store=True,
        index=True)
    employee_departed = fields.Boolean(
        string="Holder leaving", compute="_compute_employee_departed", store=True,
        help="The employee is archived or has a departure date, past or coming: what they hold is "
             "to recover.")
    external_name = fields.Char(string="Handed to", tracking=True,
                                help="A person without an employee record: contractor, volunteer.")
    external_phone = fields.Char(string="Phone")
    external_employer = fields.Char(string="Employer")
    holder_label = fields.Char(string="Held by", compute="_compute_holder_label", store=True)
    date_issued = fields.Datetime(string="Handed out", required=True, default=fields.Datetime.now,
                                  tracking=True)
    issued_by_id = fields.Many2one("res.users", string="Handed out by",
                                   default=lambda self: self.env.user)
    date_due = fields.Date(string="Expected back", tracking=True,
                           help="The end of the need. Past it, a reminder goes to the holder.")
    date_returned = fields.Datetime(string="Returned", tracking=True, copy=False)
    received_by_id = fields.Many2one("res.users", string="Received by", copy=False)
    end_reason = fields.Selection(END_REASONS, string="Ended", tracking=True, copy=False)
    state = fields.Selection([("open", "Held"), ("closed", "Ended")],
                             compute="_compute_state", store=True)
    is_overdue = fields.Boolean(string="Overdue", compute="_compute_is_overdue",
                                search="_search_is_overdue")
    signature = fields.Binary(string="Signature", attachment=True, copy=False)
    signed_by = fields.Char(string="Signed by", copy=False)
    signed_on = fields.Datetime(string="Signed on", copy=False)
    currency_id = fields.Many2one(related="company_id.currency_id")
    deposit_amount = fields.Monetary(string="Deposit")
    deposit_refunded = fields.Boolean(string="Deposit refunded", tracking=True)
    card_deactivated = fields.Boolean(
        string="Deactivated in the access system", tracking=True,
        help="For a card, fob or remote: switched off in the access control system.")
    reminded = fields.Boolean(string="Reminder sent", copy=False)
    request_id = fields.Many2one("bf.key.request", string="Request", copy=False)
    anonymized = fields.Boolean(copy=False, readonly=True)
    company_id = fields.Many2one("res.company", related="copy_id.company_id", store=True)
    note = fields.Text()

    @api.depends("employee_id.user_id")
    def _compute_holder_user_id(self):
        for assignment in self:
            assignment.holder_user_id = assignment.employee_id.user_id

    @api.depends("employee_id.active", "employee_id.departure_date")
    def _compute_employee_departed(self):
        for assignment in self:
            employee = assignment.employee_id.with_context(active_test=False)
            assignment.employee_departed = bool(employee and (not employee.active or employee.departure_date))

    @api.depends("employee_id.name", "external_name", "external_employer", "anonymized")
    def _compute_holder_label(self):
        for assignment in self:
            if assignment.employee_id:
                assignment.holder_label = assignment.employee_id.name
            elif assignment.anonymized:
                # Stored: written in the company's language, not the scheduled job's.
                lang = assignment.company_id.partner_id.lang or "en_US"
                assignment.holder_label = assignment.with_context(lang=lang).env._("(erased)")
            else:
                assignment.holder_label = " · ".join(
                    filter(None, [assignment.external_name, assignment.external_employer]))

    @api.depends("date_returned")
    def _compute_state(self):
        for assignment in self:
            assignment.state = "closed" if assignment.date_returned else "open"

    @api.depends("date_due", "date_returned")
    def _compute_is_overdue(self):
        today = fields.Date.context_today(self)
        for assignment in self:
            assignment.is_overdue = bool(
                not assignment.date_returned and assignment.date_due and assignment.date_due < today)

    def _search_is_overdue(self, operator, value):
        today = fields.Date.context_today(self)
        domain = [("date_returned", "=", False), ("date_due", "!=", False), ("date_due", "<", today)]
        positive = (operator == "=" and value) or (operator == "!=" and not value)
        return domain if positive else ["!"] + ["&", "&"] + domain

    @api.depends("copy_id", "holder_label")
    def _compute_display_name(self):
        for assignment in self:
            assignment.display_name = f"{assignment.copy_id.display_name} → {assignment.holder_label or ''}"

    # ------------------------------------------------------------------
    @api.constrains("employee_id", "external_name", "anonymized")
    def _check_holder(self):
        for assignment in self:
            if not assignment.anonymized and not (assignment.employee_id or assignment.external_name):
                raise ValidationError(_("A hand-out needs an employee, or the name of the person "
                                        "who receives the item."))

    @api.constrains("copy_id", "date_returned")
    def _check_single_open(self):
        for assignment in self.filtered(lambda a: not a.date_returned):
            others = self.sudo().search_count([
                ("copy_id", "=", assignment.copy_id.id), ("date_returned", "=", False),
                ("id", "!=", assignment.id)])
            if others:
                raise ValidationError(_(
                    "%s is already handed out. Record its return before handing it out again: "
                    "a copy never goes directly from one person to another.",
                    assignment.copy_id.display_name))

    @api.constrains("date_issued", "date_returned")
    def _check_dates(self):
        for assignment in self:
            if assignment.date_returned and assignment.date_returned < assignment.date_issued:
                raise ValidationError(_("The return cannot come before the hand-out."))

    # What makes the history: written at creation, then only by _close and the retention job.
    _FROZEN = {"copy_id", "employee_id", "external_name", "external_phone", "external_employer",
               "date_issued", "issued_by_id", "date_returned", "received_by_id", "end_reason",
               "signature", "signed_by", "signed_on", "anonymized", "request_id"}

    def write(self, vals):
        """No write moves a hand-out: not to another person, not to another copy, not back
        open. The copy comes back (_close), then a new hand-out is made."""
        # ⚠️ Only sudo lifts it, never a context key (any user sends any context by RPC).
        if not self.env.su and self._FROZEN & set(vals):
            raise UserError(_("A hand-out cannot be changed: record the return, then hand the "
                              "copy out again."))
        if "date_due" in vals:
            # A new date earns a new reminder.
            vals = dict(vals, reminded=False)
        return super().write(vals)

    def init(self):
        # The database holds the line too: two sessions handing out the same copy at once.
        self.env.cr.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS bf_key_assignment_one_open_per_copy
            ON bf_key_assignment (copy_id) WHERE date_returned IS NULL
        """)

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            for vals in vals_list:
                # Born open, handed out by the caller. date_issued may be in the past: entering
                # the keys already out is how a register starts.
                if {"date_returned", "received_by_id", "end_reason", "anonymized"} & set(vals):
                    raise UserError(_("A hand-out is created open; its return is recorded later."))
                vals["issued_by_id"] = self.env.user.id
        assignments = super().create(vals_list)
        for assignment in assignments:
            if assignment.copy_id.disposition != "active":
                raise UserError(_("%s is marked %s and cannot be handed out.",
                                  assignment.copy_id.display_name,
                                  dict(assignment.copy_id._fields["disposition"]._description_selection(
                                      self.env))[assignment.copy_id.disposition]))
        return assignments

    def copy(self, default=None):
        raise UserError(_("A hand-out cannot be duplicated: hand the copy out from its form."))

    def unlink(self):
        # The register is the proof: a hand-out is ended, never deleted.
        if not self.env.su:
            raise UserError(_("A hand-out cannot be deleted: record its return instead."))
        return super().unlink()

    # ------------------------------------------------------------------
    def _close(self, reason, when=None):
        when = when or fields.Datetime.now()
        for assignment in self:
            if assignment.date_returned:
                raise UserError(_("%s has already ended.", assignment.display_name))
            # The caller must be allowed to edit this hand-out; then the frozen fields are
            # written in sudo, by this method only.
            assignment.check_access("write")
            assignment.sudo().write({
                "date_returned": max(when, assignment.date_issued),
                "received_by_id": self.env.user.id,
                "end_reason": reason,
            })
            # The departure and overdue reminders on this hand-out have done their job.
            close_activities(assignment.sudo().activity_ids, _("Closed: %s.", dict(
                self._fields["end_reason"]._description_selection(self.env))[reason]))

    def action_return(self):
        self._close("returned")
        for assignment in self:
            if assignment.deposit_amount and not assignment.deposit_refunded:
                assignment._message_log(body=_("Deposit of %s to refund.",
                                               assignment.currency_id.format(assignment.deposit_amount)))
        return True

    def action_print_acknowledgement(self):
        return self.env.ref("bf_key_management.action_report_acknowledgement").report_action(self)

    # ------------------------------------------------------------------
    def _responsible(self):
        """Who acts on this hand-out: the key manager, else the area's first responsible."""
        self.ensure_one()
        manager = self.company_id.bf_key_manager_id
        if manager:
            return manager
        area_people = self.area_id.all_manager_ids
        if area_people:
            return area_people[0]
        group = self.env.ref("bf_key_management.group_key_manager")
        return group.users.filtered(lambda u: not u._is_superuser() and u.active)[:1]

    @api.model
    def _cron_overdue(self):
        """One reminder per hand-out past its expected return, once."""
        todo = self.env.ref("mail.mail_activity_data_todo", raise_if_not_found=False)
        for assignment in self.search([("is_overdue", "=", True), ("reminded", "=", False)]):
            user = assignment.holder_user_id or assignment._responsible()
            if not user:
                continue
            env = assignment.with_context(lang=user.lang or "en_US").env
            if assignment.holder_user_id:
                summary = env._("To return: %s", assignment.copy_id.display_name)
                note = env._("It was expected back on %s. Bring it back to the key manager.",
                             assignment.date_due)
            else:
                summary = env._("Overdue: %s", assignment.copy_id.display_name)
                # ⚠️ No holder name in the note: it outlives the retention period in the
                # "done" message. The hand-out itself says who holds it.
                note = env._("Expected back on %s. Open the hand-out to see who holds it.",
                             assignment.date_due)
            schedule_activity(
                assignment,
                activity_type_id=todo.id if todo else False, user_id=user.id,
                summary=summary, note=note)
            assignment.reminded = True

    @api.model
    def _cron_retention(self):
        """Quebec Law 25, s. 23: erase what identifies a person without an account, and the
        signature, once the purpose is served and the retention period is over."""
        now = fields.Datetime.now()
        for company in self.env["res.company"].search([("bf_key_retention_days", ">", 0)]):
            limit = now - timedelta(days=company.bf_key_retention_days)
            old = self.with_context(active_test=False).search([
                ("company_id", "=", company.id), ("anonymized", "=", False),
                ("date_returned", "!=", False), ("date_returned", "<", limit)])
            names = {a.id: a.external_name for a in old if not a.employee_id and a.external_name}
            for assignment in old:
                values = {"signature": False, "signed_by": False, "anonymized": True}
                if not assignment.employee_id:
                    values.update({"external_name": False, "external_phone": False,
                                   "external_employer": False})
                assignment.with_context(mail_notrack=True).sudo().write(values)
            # The messages that quoted the name (activity notices carry the record's title,
            # which held the name): erased with it.
            Message = self.env["mail.message"].sudo()
            for res_id, name in names.items():
                messages = Message.search([("model", "=", self._name), ("res_id", "=", res_id)])
                messages.filtered(lambda m: name.lower() in (m.body or "").lower()).unlink()
                # The others keep the record's title of the day, which held the name.
                assignment = self.browse(res_id)
                messages.exists().write({"record_name": assignment.display_name,
                                         "subject": False})
            if old:
                # The tracking messages still carry the names: erase them too.
                self.env["mail.tracking.value"].sudo().search([
                    ("mail_message_id.model", "=", self._name),
                    ("mail_message_id.res_id", "in", old.filtered(lambda a: not a.employee_id).ids),
                    ("field_id.name", "in", ["external_name"]),
                ]).unlink()
