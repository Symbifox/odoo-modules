# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import re

from odoo import api, fields, models
from odoo.exceptions import ValidationError

from .hosting_lang import reader_env


class HostingServer(models.Model):
    _name = "hosting.server"
    _description = "Hosting server"
    _order = "name"
    _inherit = ["mail.thread", "mail.activity.mixin"]

    name = fields.Char(
        string="Server name",
        required=True,
        tracking=True,
        help="Friendly server name (e.g. \"Production 1\")",
    )
    code = fields.Char(
        string="Code",
        required=True,
        help="Short identifier (e.g. \"SRV1\")",
    )
    hostname = fields.Char(
        string="Hostname",
        required=True,
        tracking=True,
        help="Fully qualified hostname (e.g. \"server1.example.com\")",
    )
    server_type = fields.Selection(
        selection=[
            ("production", "Production"),
            ("staging", "Staging"),
            ("development", "Development"),
        ],
        string="Server type",
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
            ("on_premise", "On-premises"),
            ("other", "Other"),
        ],
        string="Provider",
        tracking=True,
    )
    location = fields.Char(
        string="Location",
        help="Physical location or data center (e.g. \"Canada\", \"EU-West\")",
    )
    ip_address = fields.Char(
        string="IP address",
        help="Main IP address of the server",
    )
    ssh_user = fields.Char(
        string="SSH user",
        default="root",
    )
    ssh_port = fields.Integer(
        string="SSH port",
        default=22,
    )
    state = fields.Selection(
        selection=[
            ("active", "Active"),
            ("maintenance", "Maintenance"),
            ("decommissioned", "Decommissioned"),
        ],
        string="State",
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
        string="Service count",
        compute="_compute_service_count",
    )

    # Informations supplémentaires
    notes = fields.Html(
        string="Notes",
    )
    active = fields.Boolean(
        string="Active",
        default=True,
    )

    _sql_constraints = [
        ("code_uniq", "UNIQUE(code)", "The server code must be unique!"),
        ("hostname_uniq", "UNIQUE(hostname)", "The hostname must be unique!"),
    ]

    _HOSTNAME_RE = re.compile(r"^[a-zA-Z0-9]([a-zA-Z0-9._-]{0,253}[a-zA-Z0-9])?$")

    @api.constrains("hostname")
    def _check_hostname(self):
        for record in self:
            if record.hostname and not self._HOSTNAME_RE.match(record.hostname):
                raise ValidationError(self.env._(
                    "The hostname \"%s\" contains characters that are not allowed. "
                    "Only letters, digits, dots, hyphens and underscores are permitted.",
                    record.hostname,
                ))

    @api.constrains("ip_address")
    def _check_ip_address(self):
        for record in self:
            if record.ip_address:
                try:
                    import ipaddress as _ipa
                    _ipa.ip_address(record.ip_address)
                except ValueError:
                    raise ValidationError(self.env._(
                        "\"%s\" is not a valid IP address.", record.ip_address
                    ))

    _AUDIT_FIELDS = {"hostname", "ip_address", "state", "provider", "server_type"}

    def write(self, vals):
        tracked = self._AUDIT_FIELDS & set(vals)
        if tracked:
            AuditLog = self.env["hosting.audit.log"]
            # Journal d'audit : relu par les opérateurs, dans la langue de la
            # société (installée, sinon en_US : une langue désactivée ferait lever
            # `_description_selection`) ; libellés d'état lus sur le même env (pièges 3 et 60).
            env = reader_env(self.env)
            state_labels = dict(self._fields["state"]._description_selection(env))
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
                            description=env._(
                                "Server %(server)s: %(field)s '%(old)s' → '%(new)s'",
                                server=rec.name, field=fname, old=old_val, new=new_val,
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
            "name": self.env._("Services - %s", self.name),
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
