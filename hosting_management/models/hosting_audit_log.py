# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import json
import logging

from odoo import api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class HostingAuditLog(models.Model):
    _name = "hosting.audit.log"
    _description = "Journal d'audit d'hébergement"
    _order = "event_date desc, id desc"
    _rec_name = "display_name"

    # -- Core fields (all readonly) --
    event_date = fields.Datetime(
        string="Date",
        required=True,
        readonly=True,
        default=fields.Datetime.now,
        index=True,
    )
    user_id = fields.Many2one(
        comodel_name="res.users",
        string="Utilisateur",
        required=True,
        readonly=True,
        index=True,
    )
    action_type = fields.Selection(
        selection=[
            ("create", "Création"),
            ("write", "Modification"),
            ("unlink", "Suppression"),
            ("state_change", "Changement d'état"),
            ("access", "Accès"),
            ("credential_view", "Consultation d'identifiant"),
            ("credential_rotate", "Rotation d'identifiant"),
            ("config_change", "Changement de configuration"),
            ("maintenance", "Maintenance"),
            ("health_check", "Vérification de santé"),
            ("export", "Exportation"),
            ("security_event", "Événement de sécurité"),
        ],
        string="Type d'action",
        required=True,
        readonly=True,
        index=True,
    )
    category = fields.Selection(
        selection=[
            ("access", "Accès"),
            ("config", "Configuration"),
            ("ops", "Opérations"),
            ("security", "Sécurité"),
        ],
        string="Catégorie",
        required=True,
        readonly=True,
        index=True,
    )

    # -- Target fields --
    res_model = fields.Char(
        string="Modèle",
        readonly=True,
        index=True,
    )
    res_id = fields.Integer(
        string="ID de l'enregistrement",
        readonly=True,
    )
    res_name = fields.Char(
        string="Nom de l'enregistrement",
        readonly=True,
    )
    service_id = fields.Many2one(
        comodel_name="hosting.service",
        string="Service",
        readonly=True,
        index=True,
        ondelete="set null",
    )
    server_id = fields.Many2one(
        comodel_name="hosting.server",
        string="Serveur",
        readonly=True,
        ondelete="set null",
    )

    # -- Change details --
    description = fields.Text(
        string="Description",
        readonly=True,
    )
    old_value = fields.Text(
        string="Ancienne valeur",
        readonly=True,
    )
    new_value = fields.Text(
        string="Nouvelle valeur",
        readonly=True,
    )
    field_name = fields.Char(
        string="Champ modifié",
        readonly=True,
    )

    # -- Severity / status --
    severity = fields.Selection(
        selection=[
            ("info", "Info"),
            ("warning", "Avertissement"),
            ("critical", "Critique"),
        ],
        string="Sévérité",
        default="info",
        required=True,
        readonly=True,
    )
    status = fields.Selection(
        selection=[
            ("success", "Succès"),
            ("failure", "Échec"),
        ],
        string="Statut",
        default="success",
        required=True,
        readonly=True,
    )

    # -- Related / computed --
    partner_id = fields.Many2one(
        related="service_id.partner_id",
        string="Client",
        store=True,
        readonly=True,
    )
    display_name = fields.Char(
        string="Nom",
        compute="_compute_display_name",
        store=True,
    )

    @api.depends("action_type", "res_name", "event_date")
    def _compute_display_name(self):
        action_labels = dict(self._fields["action_type"].selection)
        for rec in self:
            label = action_labels.get(rec.action_type, rec.action_type or "")
            name = rec.res_name or ""
            rec.display_name = f"{label} - {name}" if name else label

    # -- Immutability enforcement --

    def write(self, vals):
        raise UserError(
            "Les entrées du journal d'audit ne peuvent pas être modifiées."
        )

    def unlink(self):
        raise UserError(
            "Les entrées du journal d'audit ne peuvent pas être supprimées."
        )

    # -- Centralized logging helper --

    @api.model
    def _log_event(self, action_type, category, description,
                   res_model=None, res_id=None, res_name=None,
                   service_id=None, server_id=None,
                   field_name=None, old_value=None, new_value=None,
                   severity="info", status="success"):
        """Create an immutable audit log entry.

        Called via sudo() so ACL (no create permission) is bypassed.
        """
        vals = {
            "event_date": fields.Datetime.now(),
            "user_id": self.env.uid,
            "action_type": action_type,
            "category": category,
            "description": description,
            "res_model": res_model,
            "res_id": res_id,
            "res_name": res_name,
            "service_id": service_id,
            "server_id": server_id,
            "field_name": field_name,
            "severity": severity,
            "status": status,
        }
        if old_value is not None:
            vals["old_value"] = (
                json.dumps(old_value, default=str, ensure_ascii=False)
                if not isinstance(old_value, str) else old_value
            )
        if new_value is not None:
            vals["new_value"] = (
                json.dumps(new_value, default=str, ensure_ascii=False)
                if not isinstance(new_value, str) else new_value
            )
        try:
            return self.sudo().with_context(audit_log_create=True).create(vals)
        except Exception:
            _logger.exception("Failed to create audit log entry")
            return self.browse()

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.context.get("audit_log_create"):
            raise UserError(
                "Les entrées du journal d'audit ne peuvent être créées "
                "que par le système."
            )
        return super().create(vals_list)

    # -- Archive cron --

    @api.model
    def _cron_archive_old_logs(self):
        """Delete audit logs older than the configured retention period."""
        ICP = self.env["ir.config_parameter"].sudo()
        months = int(ICP.get_param("hosting.audit_log_retention_months", "24"))
        if months <= 0:
            return
        self.env.cr.execute(
            """
            DELETE FROM hosting_audit_log
            WHERE event_date < NOW() - INTERVAL '%s months'
            """,
            [months],
        )
        deleted = self.env.cr.rowcount
        if deleted:
            _logger.info("Archived %d audit log entries older than %d months",
                         deleted, months)
