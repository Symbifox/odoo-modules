from odoo import api, fields, models

PRIORITY_DELAYS = {
    "high": 3,
    "medium": 7,
    "low": 14,
}


class AuditWatchpoint(models.Model):
    _name = "audit.watchpoint"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _description = "Point de vigilance audit TI"
    _order = "priority desc, id"

    client_id = fields.Many2one(
        "audit.client", string="Client", required=True, ondelete="cascade"
    )
    element_id = fields.Many2one("audit.element", string="Élément")
    description = fields.Text(string="Description", required=True)
    priority = fields.Selection(
        [("high", "Haute"), ("medium", "Moyenne"), ("low", "Basse")],
        string="Priorité",
        default="medium",
        tracking=True,
    )
    source = fields.Selection(
        [
            ("audit_initial", "Audit initial"),
            ("supplier_response", "Réponse fournisseur"),
            ("custom", "Personnalisé"),
        ],
        string="Source",
        default="custom",
    )
    state = fields.Selection(
        [("open", "Ouvert"), ("in_progress", "En cours"), ("resolved", "Résolu")],
        string="État",
        default="open",
        tracking=True,
    )
    assigned_to = fields.Many2one(
        "res.users", string="Assigné à", tracking=True
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
            elem_label = (
                f" \u2014 {rec.element_id.display_name}"
                if rec.element_id
                else ""
            )
            rec.activity_schedule(
                "audit_ti.mail_activity_type_audit_watchpoint",
                date_deadline=deadline,
                summary=f"Suivi : {rec.client_id.name}{elem_label}",
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
                        acts.action_feedback(feedback="Résolu")
        elif "assigned_to" in vals or "priority" in vals:
            self._cancel_watchpoint_activities()
            self.filtered(
                lambda r: r.assigned_to and r.state != "resolved"
            )._schedule_watchpoint_activity()
        return res
