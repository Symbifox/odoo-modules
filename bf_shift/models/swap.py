from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from ..lib import engine
from .tools import (check_own, guard_employee_vals, internal, is_shift_manager,
                    notify_each_in_their_language, to_local, tz_of)

EMPLOYEE_FIELDS = {"requester_id", "assignment_id", "target_id", "target_assignment_id", "reason"}


class BfShiftSwap(models.Model):
    """Swap or give away a shift between two employees.

    With a shift in return, it is an exchange; without one, the colleague
    takes the shift. The colleague accepts first, then a manager approves
    when the working conditions ask for it.
    """

    _name = "bf.shift.swap"
    _description = "Shift swap"
    _inherit = ["mail.thread"]
    _order = "create_date desc, id desc"

    name = fields.Char(compute="_compute_name", store=True)
    requester_id = fields.Many2one("hr.employee", required=True, tracking=True,
                                   default=lambda self: self.env.user.employee_id)
    requester_user_id = fields.Many2one("res.users", compute="_compute_users", store=True,
                                        compute_sudo=True)
    assignment_id = fields.Many2one(
        "bf.shift.assignment", string="Shift given", required=True,
        domain="[('employee_id', '=', requester_id), ('state', '=', 'planned'),"
               " ('schedule_state', '=', 'published')]")
    target_id = fields.Many2one("hr.employee", string="Colleague", required=True, tracking=True)
    target_user_id = fields.Many2one("res.users", compute="_compute_users", store=True,
                                     compute_sudo=True)
    target_assignment_id = fields.Many2one(
        "bf.shift.assignment", string="Shift taken in return",
        domain="[('employee_id', '=', target_id), ('state', '=', 'planned'),"
               " ('schedule_state', '=', 'published')]",
        help="Empty: the colleague takes the shift without giving one back.")
    company_id = fields.Many2one(related="assignment_id.company_id", store=True)
    state = fields.Selection([("draft", "Draft"), ("colleague", "Waiting for the colleague"),
                              ("approval", "Waiting for approval"), ("done", "Done"),
                              ("refused", "Refused"), ("cancelled", "Cancelled")],
                             default="draft", required=True, tracking=True)
    reason = fields.Char()
    check_summary = fields.Text("Checks", readonly=True)
    approved_by = fields.Many2one("res.users", readonly=True)

    @api.depends("requester_id", "target_id", "assignment_id")
    def _compute_name(self):
        for rec in self:
            rec.name = _("%(a)s → %(b)s", a=rec.requester_id.name or "",
                         b=rec.target_id.name or "")

    @api.depends("requester_id.user_id", "target_id.user_id")
    def _compute_users(self):
        for rec in self:
            rec.requester_user_id = rec.requester_id.user_id
            rec.target_user_id = rec.target_id.user_id

    @api.constrains("requester_id", "target_id", "assignment_id", "target_assignment_id")
    def _check_parties(self):
        for rec in self:
            if rec.requester_id == rec.target_id:
                raise ValidationError(_("A swap is between two different people."))
            if rec.requester_id.sudo().company_id != rec.target_id.sudo().company_id:
                raise ValidationError(_("A swap stays within one company."))
            if rec.assignment_id.employee_id != rec.requester_id:
                raise ValidationError(_("The shift given must be the requester's."))
            if rec.target_assignment_id and rec.target_assignment_id.employee_id != rec.target_id:
                raise ValidationError(_("The shift taken in return must be the colleague's."))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            guard_employee_vals(self.env, vals, EMPLOYEE_FIELDS)
        records = super().create(vals_list)
        manager = is_shift_manager(self.env)
        for rec in records:
            if not manager and rec.requester_id.sudo().user_id != self.env.user:
                raise UserError(_("You can only ask to swap your own shifts."))
        return records

    def write(self, vals):
        if not is_shift_manager(self.env):
            if set(vals) - {"reason", "target_id", "target_assignment_id", "assignment_id"} or \
                    any(rec.state != "draft" for rec in self):
                raise UserError(_("A swap moves on with its buttons."))
        res = super().write(vals)
        check_own(self, "requester_id")
        return res

    # ------------------------------------------------------------------

    def _who(self):
        """'requester', 'target' or 'manager' for the current user."""
        user = self.env.user
        if user.has_group("bf_shift.group_shift_manager"):
            return "manager"
        if self.requester_id.sudo().user_id == user:
            return "requester"
        if self.target_id.sudo().user_id == user:
            return "target"
        return None

    def action_submit(self):
        for rec in self:
            if rec._who() not in ("requester", "manager"):
                raise UserError(_("Only the requester sends the request."))
            if rec.state != "draft":
                raise UserError(_("Already sent."))
            rec.sudo().state = "colleague"
            user = rec.target_id.sudo().user_id
            if user:
                notify_each_in_their_language(rec.sudo(), user.partner_id, lambda env, rec=rec: (
                    env._("%(name)s asks you to take a shift: %(when)s",
                          name=rec.requester_id.name, when=rec.assignment_id._when_label()),
                    rec.name,
                    env._("Shift swap"),
                ))
        return True

    def action_colleague_accept(self):
        for rec in self:
            if rec._who() not in ("target", "manager"):
                raise UserError(_("Only the colleague asked can accept."))
            if rec.state != "colleague":
                raise UserError(_("This request is not waiting for the colleague."))
            agreement = rec.requester_id.sudo().shift_agreement_id
            rec.sudo().check_summary = rec._simulate()
            if agreement and not agreement.swap_requires_approval:
                rec.sudo()._apply()
            else:
                rec.sudo().state = "approval"
        return True

    def action_colleague_refuse(self):
        for rec in self:
            if rec._who() not in ("target", "manager"):
                raise UserError(_("Only the colleague asked can refuse."))
            if rec.state != "colleague":
                raise UserError(_("This request is not waiting for the colleague."))
            rec.sudo().state = "refused"
            rec.sudo().message_post(body=_("The colleague refused."))
        return True

    def action_approve(self):
        for rec in self:
            if rec._who() != "manager":
                raise UserError(_("Only a shift manager approves a swap."))
            if rec.state != "approval":
                raise UserError(_("This request is not waiting for approval."))
            rec.check_summary = rec._simulate()
            rec._apply()
        return True

    def action_refuse(self):
        for rec in self:
            if rec._who() != "manager":
                raise UserError(_("Only a shift manager refuses a swap."))
            if rec.state not in ("colleague", "approval"):
                raise UserError(_("This request is closed."))
            rec.state = "refused"
        return True

    def action_cancel(self):
        for rec in self:
            if rec._who() not in ("requester", "manager"):
                raise UserError(_("Only the requester cancels the request."))
            if rec.state in ("done", "refused", "cancelled"):
                raise UserError(_("This request is closed."))
            rec.sudo().state = "cancelled"
        return True

    def _apply(self):
        self.ensure_one()
        given = self.assignment_id
        taken = self.target_assignment_id
        if given.employee_id != self.requester_id or given.state != "planned" or (
                taken and (taken.employee_id != self.target_id or taken.state != "planned")):
            raise UserError(_("The shifts changed since the request: it cannot be applied."))
        reason = _("Swap %(name)s", name=self.name)
        internal(given, consent="given", reason=reason).write({"employee_id": self.target_id.id})
        if taken:
            internal(taken, consent="given", reason=reason).write({"employee_id": self.requester_id.id})
        self.write({"state": "done", "approved_by": self.env.user.id})
        self.message_post(body=_("Swap applied."))

    def _simulate(self):
        """Warnings the swap would raise for each of the two people."""
        self.ensure_one()
        lines = []
        pairs = [(self.target_id, self.assignment_id, self.target_assignment_id),
                 (self.requester_id, self.target_assignment_id, self.assignment_id)]
        Assignment = self.env["bf.shift.assignment"].sudo()
        for employee, gained, lost in pairs:
            if not gained:
                continue
            emp = employee.sudo()
            tz = tz_of(emp, self.env)
            params = emp.shift_agreement_id._effective_params()
            first = engine.week_start_of(gained.date, params.week_start) - timedelta(days=1)
            last = first + timedelta(days=9)
            others = Assignment.search([
                ("employee_id", "=", emp.id), ("state", "!=", "cancelled"),
                ("date", ">=", first), ("date", "<=", last),
                ("id", "not in", (lost | gained).ids),
            ])
            segments = [a._to_segment(tz) for a in others]
            segments.append(gained.sudo()._to_segment(tz, informed_at=to_local(
                fields.Datetime.now(), tz)))
            warns = engine.check_segments(
                segments, params, usual_day_hours=emp._shift_usual_day_hours(),
                flexible=emp.shift_flexible_hours,
                availability_required=True,  # a swap is asked for, not imposed
                unavailable=self.env["bf.shift.availability"].sudo()._unavailable_intervals(
                    emp, first, last),
                only_keys={gained.id})
            for w in warns:
                lines.append("%s: %s" % (emp.name, gained._warning_message(w)))
        return "\n".join(lines) or _("No warning.")
