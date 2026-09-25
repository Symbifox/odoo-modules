from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .tools import post

CONSENT_FIELDS = {"consent", "consent_at", "consent_recorded_by", "consent_note"}


class BfShiftChange(models.Model):
    """Change log of published schedules.

    Written by the module only, never edited, never deleted: in a grievance
    it is the evidence of who was told what, and when. Only the consent can
    be recorded afterwards, once.
    """

    _name = "bf.shift.change"
    _description = "Schedule change"
    _order = "create_date desc, id desc"
    _rec_name = "after"

    assignment_id = fields.Many2one("bf.shift.assignment", required=True, ondelete="cascade",
                                    index=True)
    schedule_id = fields.Many2one("bf.shift.schedule", required=True, ondelete="cascade",
                                  index=True)
    employee_id = fields.Many2one("hr.employee", string="Employee concerned")
    previous_employee_id = fields.Many2one("hr.employee", string="Previous employee")
    employee_user_id = fields.Many2one("res.users", compute="_compute_users", store=True,
                                       compute_sudo=True)
    previous_user_id = fields.Many2one("res.users", compute="_compute_users", store=True,
                                       compute_sudo=True)
    change_type = fields.Selection([("create", "Added"), ("modify", "Changed"),
                                    ("reassign", "Reassigned"), ("cancel", "Cancelled")],
                                   required=True)
    before = fields.Char()
    after = fields.Char(required=True)
    reason = fields.Char()
    user_id = fields.Many2one("res.users", string="Changed by", default=lambda self: self.env.user,
                              required=True)
    late = fields.Boolean("Short notice",
                          help="Made fewer days ahead than the notice rule: the employee may refuse it.")
    consent = fields.Selection([("na", "Not needed"), ("pending", "To record"),
                                ("given", "Accepted"), ("refused", "Refused")],
                               default="na", required=True)
    consent_at = fields.Datetime("Answered on")
    consent_recorded_by = fields.Many2one("res.users")
    consent_note = fields.Char("Answer note")

    @api.depends("employee_id.user_id", "previous_employee_id.user_id")
    def _compute_users(self):
        for rec in self:
            rec.employee_user_id = rec.employee_id.user_id
            rec.previous_user_id = rec.previous_employee_id.user_id

    company_id = fields.Many2one(related="schedule_id.company_id", store=True)

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            raise UserError(_("The change log is written by the module only."))
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su:
            raise UserError(_("The change log cannot be edited."))
        if set(vals) - CONSENT_FIELDS:
            raise UserError(_("The change log cannot be edited."))
        if any(rec.consent in ("given", "refused") for rec in self):
            raise UserError(_("The answer to this change is already recorded."))
        return super().write(vals)

    def unlink(self):
        raise UserError(_("The change log cannot be deleted."))

    def _answer(self, answer, note=False):
        for rec in self:
            if rec.consent != "pending":
                raise UserError(_("No answer is expected for this change."))
            role = rec.assignment_id._check_is_employee_or_manager(rec.employee_id)
            rec.sudo().write({
                "consent": answer,
                "consent_at": fields.Datetime.now(),
                "consent_recorded_by": self.env.user.id,
                "consent_note": note or (role == "manager" and _("Recorded by a manager")) or False,
            })
            post(rec.schedule_id.sudo(), body=_(
                "%(employee)s %(answer)s the change: %(after)s",
                employee=rec.employee_id.name,
                answer=_("accepted") if answer == "given" else _("refused"),
                after=rec.after))

    def action_accept(self):
        self._answer("given")
        return True

    def action_refuse(self):
        self._answer("refused")
        return True
