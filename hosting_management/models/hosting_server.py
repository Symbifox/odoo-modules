# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import re

from odoo import api, fields, models
from odoo.exceptions import ValidationError


class HostingServer(models.Model):
    _name = "hosting.server"
    _description = "Serveur d'hébergement"
    _order = "name"
    _inherit = ["mail.thread", "mail.activity.mixin"]

    name = fields.Char(
        string="Nom du serveur",
        required=True,
        tracking=True,
        help="Nom convivial du serveur (ex. : « Production 1 »)",
    )
    code = fields.Char(
        string="Code",
        required=True,
        help="Identifiant court (ex. : « PT1 »)",
    )
    hostname = fields.Char(
        string="Nom d'hôte",
        required=True,
        tracking=True,
        help="Nom d'hôte pleinement qualifié (ex. : « server1.example.com »)",
    )
    server_type = fields.Selection(
        selection=[
            ("production", "Production"),
            ("staging", "Préproduction"),
            ("development", "Développement"),
        ],
        string="Type de serveur",
        default="production",
        required=True,
        tracking=True,
    )
    provider = fields.Selection(
        selection=[
            ("hetzner", "Hetzner"),
            ("ovh", "OVH"),
            ("aws", "AWS"),
            ("azure", "Azure"),
            ("gcp", "Google Cloud"),
            ("digitalocean", "DigitalOcean"),
            ("on_premise", "Sur site"),
            ("other", "Autre"),
        ],
        string="Fournisseur",
        tracking=True,
    )
    location = fields.Char(
        string="Emplacement",
        help="Emplacement physique ou centre de données (ex. : « Canada », « EU-West »)",
    )
    ip_address = fields.Char(
        string="Adresse IP",
        help="Adresse IP principale du serveur",
    )
    ssh_user = fields.Char(
        string="Utilisateur SSH",
        default="root",
    )
    ssh_port = fields.Integer(
        string="Port SSH",
        default=22,
    )
    state = fields.Selection(
        selection=[
            ("active", "Actif"),
            ("maintenance", "Maintenance"),
            ("decommissioned", "Décommissionné"),
        ],
        string="État",
        default="active",
        required=True,
        tracking=True,
    )

    # Relations
    service_ids = fields.One2many(
        comodel_name="hosting.service",
        inverse_name="server_id",
        string="Services",
    )
    service_count = fields.Integer(
        string="Nombre de services",
        compute="_compute_service_count",
    )

    # Informations supplémentaires
    notes = fields.Html(
        string="Notes",
    )
    active = fields.Boolean(
        string="Actif",
        default=True,
    )

    _sql_constraints = [
        ("code_uniq", "UNIQUE(code)", "Le code du serveur doit être unique !"),
        ("hostname_uniq", "UNIQUE(hostname)", "Le nom d'hôte doit être unique !"),
    ]

    _HOSTNAME_RE = re.compile(r"^[a-zA-Z0-9]([a-zA-Z0-9._-]{0,253}[a-zA-Z0-9])?$")

    @api.constrains("hostname")
    def _check_hostname(self):
        for record in self:
            if record.hostname and not self._HOSTNAME_RE.match(record.hostname):
                raise ValidationError(
                    f"Le nom d'hôte « {record.hostname} » contient des caractères non autorisés. "
                    "Seuls les lettres, chiffres, points, tirets et underscores sont permis."
                )

    @api.constrains("ip_address")
    def _check_ip_address(self):
        for record in self:
            if record.ip_address:
                try:
                    import ipaddress as _ipa
                    _ipa.ip_address(record.ip_address)
                except ValueError:
                    raise ValidationError(
                        f"« {record.ip_address} » n'est pas une adresse IP valide."
                    )

    _AUDIT_FIELDS = {"hostname", "ip_address", "state", "provider", "server_type"}

    def write(self, vals):
        tracked = self._AUDIT_FIELDS & set(vals)
        if tracked:
            AuditLog = self.env["hosting.audit.log"]
            state_labels = dict(self._fields["state"].selection)
            for rec in self:
                for fname in tracked:
                    old_val = getattr(rec, fname)
                    new_val = vals[fname]
                    if old_val != new_val:
                        severity = "info"
                        if fname == "state":
                            old_val = state_labels.get(old_val, old_val)
                            new_val = state_labels.get(new_val, new_val)
                            if vals["state"] in ("maintenance", "decommissioned"):
                                severity = "warning"
                        AuditLog._log_event(
                            action_type="config_change",
                            category="config",
                            description=(
                                f"Serveur {rec.name} : {fname} "
                                f"'{old_val}' → '{new_val}'"
                            ),
                            res_model=self._name,
                            res_id=rec.id,
                            res_name=rec.display_name,
                            server_id=rec.id,
                            field_name=fname,
                            old_value=str(old_val) if old_val else "",
                            new_value=str(new_val) if new_val else "",
                            severity=severity,
                        )
        return super().write(vals)

    @api.depends("service_ids")
    def _compute_service_count(self):
        for record in self:
            record.service_count = len(record.service_ids)

    def action_view_services(self):
        """Ouvrir les services hébergés sur ce serveur."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": f"Services - {self.name}",
            "res_model": "hosting.service",
            "views": [[False, "list"], [False, "kanban"], [False, "form"]],
            "domain": [("server_id", "=", self.id)],
            "context": {"default_server_id": self.id},
        }

    def action_set_active(self):
        """Mettre le serveur à l'état actif."""
        self.write({"state": "active"})

    def action_set_maintenance(self):
        """Mettre le serveur en état de maintenance."""
        self.write({"state": "maintenance"})

    def action_decommission(self):
        """Décommissionner le serveur."""
        self.write({"state": "decommissioned"})
