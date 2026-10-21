# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""hosting.license.seat — Allocation individuelle d'un siège de licence."""
import logging

from odoo import api, fields, models

from .hosting_lang import reader_env

_logger = logging.getLogger(__name__)


class HostingLicenseSeat(models.Model):
    _name = "hosting.license.seat"
    _description = "License seat (individual allocation)"
    _order = "license_id, sequence, id"
    _inherit = ["mail.thread"]

    license_id = fields.Many2one(
        comodel_name="hosting.license",
        string="Pool",
        required=True,
        ondelete="cascade",
        tracking=True,
        index=True,
    )
    sequence = fields.Integer(default=10)

    product_key = fields.Char(
        string="Product key",
        groups="hosting_management.group_hosting_manager",
        help="Product key. Visible to managers only. Not tracked, to "
             "avoid leaking it in mail.tracking.value.",
    )

    state = fields.Selection(
        selection=[
            ("free", "Unassigned"),
            ("activated", "Activated"),
            ("failed", "Activation failed"),
            ("revoked", "Revoked"),
        ],
        string="State",
        default="free",
        required=True,
        tracking=True,
    )
    activated_on = fields.Date(string="Activated on", tracking=True)

    assignee_type = fields.Selection(
        selection=[
            ("free_text", "Free text"),
            ("partner", "Client"),
            ("endpoint", "Endpoint"),
            ("project", "Project"),
        ],
        string="Assignment type",
        default="free_text",
        required=True,
    )
    partner_id = fields.Many2one(
        comodel_name="res.partner",
        string="Assigned client",
        tracking=True,
        domain="[('is_company', '=', True)]",
    )
    endpoint_id = fields.Many2one(
        comodel_name="hosting.endpoint",
        string="Assigned endpoint",
        tracking=True,
    )
    project_id = fields.Many2one(
        comodel_name="project.project",
        string="Assigned project",
        tracking=True,
    )
    assignee_label = fields.Char(
        string="Assignment label",
        help="Free text when the seat is assigned outside Odoo (e.g. "
             "\"VM1\", \"Accounting laptop\").",
    )

    display_assignee = fields.Char(
        string="Assignee",
        compute="_compute_display_assignee",
        store=True,
    )
    notes = fields.Char(string="Notes")

    _SENSITIVE_FIELDS = ("product_key",)

    @api.depends(
        "assignee_type",
        "partner_id",
        "endpoint_id",
        "project_id",
        "assignee_label",
    )
    def _compute_display_assignee(self):
        for seat in self:
            if seat.assignee_type == "partner" and seat.partner_id:
                seat.display_assignee = seat.partner_id.display_name
            elif seat.assignee_type == "endpoint" and seat.endpoint_id:
                seat.display_assignee = seat.endpoint_id.display_name
            elif seat.assignee_type == "project" and seat.project_id:
                seat.display_assignee = seat.project_id.display_name
            else:
                seat.display_assignee = seat.assignee_label or ""

    @api.onchange("endpoint_id")
    def _onchange_endpoint_id(self):
        if self.endpoint_id and not self.partner_id:
            self.partner_id = self.endpoint_id.partner_id

    @api.onchange("state")
    def _onchange_state(self):
        if self.state == "activated" and not self.activated_on:
            self.activated_on = fields.Date.today()

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for rec, vals in zip(records, vals_list):
            sensitive = [f for f in self._SENSITIVE_FIELDS if vals.get(f)]
            if sensitive:
                rec._audit_sensitive_writes(sensitive, action="set")
        return records

    def write(self, vals):
        sensitive_changed = [f for f in self._SENSITIVE_FIELDS if f in vals]
        result = super().write(vals)
        if sensitive_changed:
            for rec in self:
                actions = {
                    f: ("set" if vals.get(f) else "cleared")
                    for f in sensitive_changed
                }
                rec._audit_sensitive_writes(
                    list(actions.keys()),
                    action=",".join(f"{k}={v}" for k, v in actions.items()),
                )
        return result

    def _audit_sensitive_writes(self, field_names, action):
        AuditLog = self.env.get("hosting.audit.log")
        if AuditLog is None:
            return
        # Journal d'audit, relu par l'équipe d'hébergement : langue de la société.
        env = reader_env(self.env)
        for rec in self:
            AuditLog._log_event(
                action_type="config_change",
                category="security",
                description=env._(
                    "Sensitive field changed on seat #%(id)s (%(pool)s): "
                    "%(fields)s (%(action)s). Value not logged.",
                    id=rec.id, pool=rec.license_id.name,
                    fields=", ".join(field_names), action=action,
                ),
                res_model="hosting.license.seat",
                res_id=rec.id,
                res_name=rec.display_assignee or env._("Seat #%s", rec.id),
                field_name=",".join(field_names),
                severity="warning",
                status="success",
            )
