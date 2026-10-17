from odoo import _, api, fields, models

from .bf_key_type import schedule_activity

KEY_GROUP = "bf_key_management.group_key_area_manager"


class HrEmployee(models.Model):
    _inherit = "hr.employee"

    bf_key_assignment_ids = fields.One2many(
        "bf.key.assignment", "employee_id", string="Keys held",
        domain=[("date_returned", "=", False)], groups=KEY_GROUP)
    bf_key_count = fields.Integer(string="Keys", compute="_compute_bf_key_counts", groups=KEY_GROUP)
    bf_key_incident_count = fields.Integer(
        string="Losses in 12 months", compute="_compute_bf_key_counts", groups=KEY_GROUP)

    def _compute_bf_key_counts(self):
        Assignment = self.env["bf.key.assignment"].sudo()
        Incident = self.env["bf.key.incident"].sudo()
        since = fields.Date.subtract(fields.Date.context_today(self), years=1)
        for employee in self:
            employee.bf_key_count = Assignment.search_count(
                [("employee_id", "=", employee.id), ("date_returned", "=", False)])
            employee.bf_key_incident_count = Incident.search_count(
                [("employee_id", "=", employee.id), ("date", ">=", since)])

    def write(self, vals):
        leaving = self.browse()
        if vals.get("active") is False or vals.get("departure_date"):
            leaving = self.filtered(lambda e: e.active or not e.departure_date)
        result = super().write(vals)
        if leaving:
            leaving._bf_key_departure()
        return result

    def _bf_key_departure(self):
        """One "Recover" activity per item still held, on the hand-out itself, so that it
        closes when the item comes back. Archiving is not blocked."""
        recover = self.env.ref("bf_key_management.activity_type_recover")
        Assignment = self.env["bf.key.assignment"].sudo()
        for employee in self:
            held = Assignment.search([("employee_id", "=", employee.id), ("date_returned", "=", False)])
            if not held:
                continue
            lines = []
            for assignment in held:
                user = assignment._responsible()
                if not user:
                    continue
                if assignment.activity_ids.filtered(lambda a: a.activity_type_id == recover):
                    continue
                env = assignment.with_context(lang=user.lang or "en_US").env
                electronic = assignment.kind in ("card", "fob", "remote")
                summary = env._("Recover: %s", assignment.copy_id.display_name)
                note = env._("%(who)s is leaving (departure).", who=employee.name)
                if electronic:
                    note += " " + env._("Deactivate it in the access system today, even if it "
                                        "comes back later.")
                schedule_activity(
                    assignment,
                    activity_type_id=recover.id, user_id=user.id,
                    date_deadline=employee.departure_date or fields.Date.context_today(self),
                    summary=summary, note=note)
                lines.append(assignment.copy_id.display_name)
            if lines:
                employee.sudo()._message_log(body=_(
                    "Keys to recover at departure: %s", ", ".join(lines)))

    def action_bf_key_view(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window", "name": _("Keys held"),
            "res_model": "bf.key.assignment", "view_mode": "list,form",
            "domain": [("employee_id", "=", self.id), ("date_returned", "=", False)],
            "context": {"default_employee_id": self.id},
        }
