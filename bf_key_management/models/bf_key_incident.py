from odoo import _, api, fields, models

from .bf_key_type import close_activities

DECISIONS = [
    ("pending", "To decide"),
    ("rekey", "Rekey the doors"),
    ("deactivate", "Deactivate in the access system"),
    ("accept", "Risk accepted"),
    ("found", "Found again"),
]


class BfKeyIncident(models.Model):
    """A copy lost or stolen, and what was done about it.

    The compromised doors are computed when the incident is declared and then
    kept as they were: rekeying later changes the system, not the history.
    """
    _name = "bf.key.incident"
    _description = "Lost or stolen key"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "date desc, id desc"

    name = fields.Char(compute="_compute_name", store=True)
    copy_id = fields.Many2one("bf.key.copy", string="Copy", required=True, index=True, ondelete="restrict")
    type_id = fields.Many2one("bf.key.type", related="copy_id.type_id", store=True)
    kind = fields.Selection(related="copy_id.kind", store=True)
    area_id = fields.Many2one("bf.key.area", related="type_id.area_id", store=True, index=True)
    assignment_id = fields.Many2one("bf.key.assignment", string="Hand-out", index=True)
    employee_id = fields.Many2one("hr.employee", related="assignment_id.employee_id", store=True)
    holder_label = fields.Char(string="Held by", related="assignment_id.holder_label")
    incident_type = fields.Selection([("lost", "Lost"), ("stolen", "Stolen")], string="What happened",
                                     required=True, default="lost", tracking=True)
    date = fields.Date(required=True, default=fields.Date.context_today, tracking=True)
    circumstances = fields.Text()
    police_report = fields.Char(string="Police report number")
    compromised_door_ids = fields.Many2many(
        "bf.key.door", string="Compromised doors",
        groups="bf_key_management.group_key_area_manager",
        help="Every door this copy opened when it was declared.")
    compromised_count = fields.Integer(
        compute="_compute_compromised_count", groups="bf_key_management.group_key_area_manager")
    decision = fields.Selection(DECISIONS, default="pending", required=True, tracking=True)
    decision_note = fields.Text(string="Why this decision")
    currency_id = fields.Many2one(related="company_id.currency_id")
    cost = fields.Monetary(help="What rekeying or replacing cost.")
    fee_charged = fields.Monetary(string="Fee charged")
    state = fields.Selection([("open", "Open"), ("resolved", "Resolved")], default="open",
                             required=True, tracking=True)
    resolved_on = fields.Date(string="Resolved on", readonly=True)
    company_id = fields.Many2one("res.company", related="copy_id.company_id", store=True)

    @api.depends("copy_id.display_name", "date")
    def _compute_name(self):
        # ⚠️ No translated word in a stored name: it would stay in its author's language.
        for incident in self:
            incident.name = f"{incident.copy_id.display_name or ''} ({incident.date or ''})"

    def _compute_compromised_count(self):
        for incident in self:
            incident.compromised_count = len(incident.compromised_door_ids)

    def action_resolve(self):
        for incident in self:
            incident.write({"state": "resolved", "resolved_on": fields.Date.context_today(self)})
            close_activities(incident.sudo().activity_ids, _("Resolved."))
            if incident.decision == "found" and incident.copy_id.disposition in ("lost", "stolen"):
                incident.copy_id.action_reactivate()
        return True

    def action_reopen(self):
        self.write({"state": "open", "resolved_on": False})
        return True
