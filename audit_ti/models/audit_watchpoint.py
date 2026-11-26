from odoo import api, fields, models

PRIORITY_DELAYS = {
    "high": 3,
    "medium": 7,
    "low": 14,
}


class AuditWatchpoint(models.Model):
    _name = "audit.watchpoint"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _description = "IT audit watchpoint"
    _order = "priority desc, id"

    client_id = fields.Many2one(
        "audit.client", string="Client", required=True, ondelete="cascade"
    )
    element_id = fields.Many2one("audit.element", string="Element")
    description = fields.Text(string="Description", required=True)
    priority = fields.Selection(
        [("high", "High"), ("medium", "Medium"), ("low", "Low")],
        string="Priority",
        default="medium",
        tracking=True,
    )
    source = fields.Selection(
        [
            ("audit_initial", "Initial audit"),
            ("supplier_response", "Supplier response"),
            ("custom", "Custom"),
        ],
        string="Source",
        default="custom",
    )
    state = fields.Selection(
        [("open", "Open"), ("in_progress", "In progress"), ("resolved", "Resolved")],
        string="State",
        default="open",
        tracking=True,
    )
    assigned_to = fields.Many2one(
        "res.users", string="Assigned to", tracking=True
    )

    # --- Activity helpers ---

    def _get_activity_type(self):
        return self.env.ref(
            "audit_ti.mail_activity_type_audit_watchpoint",
            raise_if_not_found=False,
        )

    def _schedule_watchpoint_activity(self):
        """Schedule an activity for the assigned user."""
        activity_type = self._get_activity_type()
        if not activity_type:
            return
        for rec in self:
            if not rec.assigned_to or rec.state == "resolved":
                continue
            delay = PRIORITY_DELAYS.get(rec.priority, 7)
            deadline = fields.Date.add(fields.Date.today(), days=delay)
            # Le résumé est ÉCRIT : dans la langue de l'assigné, pas de qui crée.
            lu = rec.with_context(lang=rec.assigned_to.lang or rec.env.lang)
            elem_label = (
                f" \u2014 {lu.element_id.display_name}"
                if rec.element_id
                else ""
            )
            rec.activity_schedule(
                "audit_ti.mail_activity_type_audit_watchpoint",
                date_deadline=deadline,
                summary=lu.env._(
                    "Follow-up: %(client)s%(element)s",
                    client=rec.client_id.name,
                    element=elem_label,
                ),
                note=rec.description or "",
                user_id=rec.assigned_to.id,
            )

    def _cancel_watchpoint_activities(self):
        """Remove pending watchpoint activities."""
        activity_type = self._get_activity_type()
        if not activity_type:
            return
        for rec in self:
            rec.activity_ids.filtered(
                lambda a, at=activity_type: a.activity_type_id == at
            ).unlink()

    # --- CRUD overrides ---

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records.filtered("assigned_to")._schedule_watchpoint_activity()
        return records

    def write(self, vals):
        res = super().write(vals)
        if vals.get("state") == "resolved":
            activity_type = self._get_activity_type()
            if activity_type:
                for rec in self:
                    acts = rec.activity_ids.filtered(
                        lambda a, at=activity_type: a.activity_type_id == at
                    )
                    if acts:
                        acts.action_feedback(feedback=rec.env._("Resolved"))
        elif "assigned_to" in vals or "priority" in vals:
            self._cancel_watchpoint_activities()
            self.filtered(
                lambda r: r.assigned_to and r.state != "resolved"
            )._schedule_watchpoint_activity()
        return res
