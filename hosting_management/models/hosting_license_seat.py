# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""hosting.license.seat — Allocation individuelle d'un siège de licence."""
import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class HostingLicenseSeat(models.Model):
    _name = "hosting.license.seat"
    _description = "Siège de licence (allocation individuelle)"
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
        string="Clé produit",
        groups="hosting_management.group_hosting_manager",
        help="Clé de produit. Visible uniquement par les gestionnaires. Pas de "
        "tracking pour éviter le leak en mail.tracking.value.",
    )

    state = fields.Selection(
        selection=[
            ("free", "Libre"),
            ("activated", "Activée"),
            ("failed", "Activation échouée"),
            ("revoked", "Révoquée"),
        ],
        string="État",
        default="free",
        required=True,
        tracking=True,
    )
    activated_on = fields.Date(string="Activée le", tracking=True)

    assignee_type = fields.Selection(
        selection=[
            ("free_text", "Texte libre"),
            ("partner", "Client"),
            ("endpoint", "Poste"),
            ("project", "Projet"),
        ],
        string="Type d'affectation",
        default="free_text",
        required=True,
    )
    partner_id = fields.Many2one(
        comodel_name="res.partner",
        string="Client affecté",
        tracking=True,
        domain="[('is_company', '=', True)]",
    )
    endpoint_id = fields.Many2one(
        comodel_name="hosting.endpoint",
        string="Poste affecté",
        tracking=True,
    )
    project_id = fields.Many2one(
        comodel_name="project.project",
        string="Projet affecté",
        tracking=True,
    )
    assignee_label = fields.Char(
        string="Libellé d'affectation",
        help="Texte libre quand le siège est affecté hors Odoo (ex. « VM1 », "
        "« Portable comptabilité »).",
    )

    display_assignee = fields.Char(
        string="Affecté à",
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
        for rec in self:
            AuditLog._log_event(
                action_type="config_change",
                category="security",
                description=(
                    f"Champ sensible modifié sur le siège #{rec.id} "
                    f"({rec.license_id.name}) : {', '.join(field_names)} ({action}). "
                    "Valeur non journalisée."
                ),
                res_model="hosting.license.seat",
                res_id=rec.id,
                res_name=rec.display_assignee or f"Seat #{rec.id}",
                field_name=",".join(field_names),
                severity="warning",
                status="success",
            )
