import random

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from .bf_key_type import close_activities, schedule_activity

RESULTS = [
    ("pending", "To check"),
    ("confirmed", "Confirmed by the holder"),
    ("sighted", "Seen by the key manager"),
    ("missing", "Missing"),
]


class BfKeyAudit(models.Model):
    """A periodic check that every copy handed out is still where the register says.

    Guides advise once a year at least, on a sample of a quarter of the holders
    or more. Holders confirm from "Keys to confirm"; the key manager records what they
    saw; whatever is left is a discrepancy.
    """
    _name = "bf.key.audit"
    _description = "Key audit"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "date desc, id desc"

    name = fields.Char(required=True, default=lambda self: _("Key audit %s", fields.Date.context_today(self)))
    date = fields.Date(required=True, default=fields.Date.context_today)
    deadline = fields.Date(string="Answer by")
    scope = fields.Selection([("all", "Every hand-out"), ("sample", "A sample")],
                             default="all", required=True)
    sample_percent = fields.Integer(string="Sample (%)", default=25)
    area_id = fields.Many2one("bf.key.area", string="Area", help="Empty: every area.")
    state = fields.Selection([("draft", "Draft"), ("running", "Running"), ("done", "Done")],
                             default="draft", required=True, tracking=True)
    line_ids = fields.One2many("bf.key.audit.line", "audit_id", string="Lines")
    line_count = fields.Integer(compute="_compute_counts")
    pending_count = fields.Integer(string="To check", compute="_compute_counts")
    missing_count = fields.Integer(string="Missing", compute="_compute_counts")
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company)

    @api.depends("line_ids.result")
    def _compute_counts(self):
        for audit in self:
            audit.line_count = len(audit.line_ids)
            audit.pending_count = len(audit.line_ids.filtered(lambda l: l.result == "pending"))
            audit.missing_count = len(audit.line_ids.filtered(lambda l: l.result == "missing"))

    def action_start(self):
        todo = self.env.ref("mail.mail_activity_data_todo", raise_if_not_found=False)
        for audit in self:
            if audit.state != "draft":
                raise UserError(_("This audit has already started."))
            domain = [("date_returned", "=", False), ("company_id", "=", audit.company_id.id)]
            if audit.area_id:
                domain.append(("area_id", "child_of", audit.area_id.id))
            assignments = self.env["bf.key.assignment"].search(domain)
            if audit.scope == "sample" and assignments:
                # Sample holders, not copies: a holder checked is checked for all they hold.
                holders = list({a.employee_id.id or f"x{a.id}" for a in assignments})
                size = max(1, round(len(holders) * max(1, min(audit.sample_percent, 100)) / 100))
                picked = set(random.sample(holders, size))
                assignments = assignments.filtered(lambda a: (a.employee_id.id or f"x{a.id}") in picked)
            if not assignments:
                raise UserError(_("Nothing is handed out in this scope."))
            lines = self.env["bf.key.audit.line"].create([
                {"audit_id": audit.id, "assignment_id": a.id} for a in assignments])
            audit.state = "running"
            for user in lines.holder_user_id:
                mine = lines.filtered(lambda l: l.holder_user_id == user)
                env = audit.with_context(lang=user.lang or "en_US").env
                schedule_activity(
                    mine[0],
                    activity_type_id=todo.id if todo else False, user_id=user.id,
                    date_deadline=audit.deadline or fields.Date.context_today(self),
                    summary=env._("Confirm the keys you hold (%s)", len(mine)),
                    note=env._("Open \"Keys to confirm\" and confirm each item you still have, or mark it "
                               "missing."))
        return True

    def action_done(self):
        for audit in self:
            audit.state = "done"
            audit.line_ids.sudo().activity_ids.unlink()
            missing = audit.line_ids.filtered(lambda l: l.result in ("missing", "pending"))
            if missing:
                audit._message_log(body=_("Discrepancies (%(count)s): %(items)s", count=len(missing),
                                          items=", ".join(missing.mapped("copy_id.display_name"))))
        return True


class BfKeyAuditLine(models.Model):
    _name = "bf.key.audit.line"
    _description = "Key audit line"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "audit_id, id"
    _mail_post_access = "read"

    audit_id = fields.Many2one("bf.key.audit", required=True, ondelete="cascade", index=True)
    # Stored, so the holder's list filters and reads it without going through the audit.
    audit_state = fields.Selection(related="audit_id.state", store=True)
    assignment_id = fields.Many2one("bf.key.assignment", required=True, ondelete="cascade", index=True)
    copy_id = fields.Many2one(related="assignment_id.copy_id")
    holder_label = fields.Char(related="assignment_id.holder_label")
    holder_user_id = fields.Many2one(related="assignment_id.holder_user_id", store=True, index=True)
    area_id = fields.Many2one(related="assignment_id.area_id", store=True, index=True)
    result = fields.Selection(RESULTS, default="pending", required=True)
    checked_by_id = fields.Many2one("res.users", string="Checked by", readonly=True)
    checked_on = fields.Datetime(string="Checked on", readonly=True)
    note = fields.Char()
    company_id = fields.Many2one(related="audit_id.company_id", store=True)

    def _record(self, result):
        self.write({"result": result, "checked_by_id": self.env.user.id,
                    "checked_on": fields.Datetime.now()})

    def _holder_answer(self, result):
        """The holder answers for their own lines; the write goes in sudo because a holder
        cannot edit audit lines, only answer this question on their own."""
        for line in self:
            if line.holder_user_id != self.env.user:
                raise AccessError(_("You can only answer for the keys you hold."))
            if line.sudo().audit_state != "running":
                raise UserError(_("This audit is closed."))
        self.sudo().write({"result": result, "checked_by_id": self.env.user.id,
                           "checked_on": fields.Datetime.now()})
        for line in self.sudo():
            siblings = line.audit_id.line_ids.filtered(
                lambda l: l.holder_user_id == line.holder_user_id and l.result == "pending")
            if not siblings:
                close_activities(line.audit_id.line_ids.filtered(
                    lambda l: l.holder_user_id == line.holder_user_id).activity_ids, _("Answered."))
        return True

    def action_holder_confirm(self):
        return self._holder_answer("confirmed")

    def action_holder_missing(self):
        return self._holder_answer("missing")

    def action_sighted(self):
        self._record("sighted")
        return True

    def action_missing(self):
        self._record("missing")
        return True
