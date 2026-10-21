# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import api, fields, models

from .hosting_lang import reader_env


class HostingSecurityEvent(models.Model):
    _name = "hosting.security.event"
    _description = "Hosting security event"
    _order = "event_date desc, id desc"
    _inherit = ["mail.thread", "mail.activity.mixin"]

    name = fields.Char(
        string="Title",
        required=True,
        tracking=True,
    )
    event_date = fields.Datetime(
        string="Event date",
        required=True,
        default=fields.Datetime.now,
        tracking=True,
    )
    event_type = fields.Selection(
        selection=[
            ("unauthorized_access", "Unauthorized access"),
            ("certificate_issue", "Certificate issue"),
            ("vulnerability", "Vulnerability"),
            ("data_breach", "Data breach"),
            ("service_compromise", "Service compromise"),
            ("credential_leak", "Credential leak"),
            ("dns_issue", "DNS issue"),
            ("backup_failure", "Backup failure"),
            ("other", "Other"),
        ],
        string="Type",
        required=True,
        default="other",
        tracking=True,
    )
    severity = fields.Selection(
        selection=[
            ("low", "Low"),
            ("medium", "Medium"),
            ("high", "High"),
            ("critical", "Critical"),
        ],
        string="Severity",
        required=True,
        default="medium",
        tracking=True,
    )
    state = fields.Selection(
        selection=[
            ("open", "Open"),
            ("investigating", "Investigating"),
            ("mitigated", "Mitigated"),
            ("resolved", "Resolved"),
            ("false_positive", "False positive"),
        ],
        string="State",
        required=True,
        default="open",
        tracking=True,
    )

    # -- Relations --
    service_ids = fields.Many2many(
        comodel_name="hosting.service",
        string="Affected services",
    )
    server_id = fields.Many2one(
        comodel_name="hosting.server",
        string="Affected server",
    )

    # -- Details --
    description = fields.Html(
        string="Incident details",
    )
    impact = fields.Html(
        string="Impact assessment",
    )
    resolution = fields.Html(
        string="Resolution notes",
    )
    resolved_date = fields.Datetime(
        string="Resolution date",
        tracking=True,
    )

    # -- Assignment --
    assigned_user_id = fields.Many2one(
        comodel_name="res.users",
        string="Assigned to",
        tracking=True,
    )
    reported_by_id = fields.Many2one(
        comodel_name="res.users",
        string="Reported by",
        default=lambda self: self.env.user,
    )

    def action_investigate(self):
        self.write({"state": "investigating"})

    def action_mitigate(self):
        self.write({"state": "mitigated"})

    def action_resolve(self):
        self.write({
            "state": "resolved",
            "resolved_date": fields.Datetime.now(),
        })

    def action_false_positive(self):
        self.write({"state": "false_positive"})

    def action_reopen(self):
        self.write({
            "state": "open",
            "resolved_date": False,
        })

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        AuditLog = self.env["hosting.audit.log"]
        Ntfy = self.env["hosting.ntfy"]
        # Journal d'audit et alerte lus par l'équipe d'hébergement : langue de la
        # société (création par une API ou un travail planifié aussi). Libellés de
        # sélection traduits, dans la même langue (piège 3, 60).
        env = reader_env(self.env)
        type_labels = dict(self._fields["event_type"]._description_selection(env))
        severity_labels = dict(self._fields["severity"]._description_selection(env))
        for rec in records:
            AuditLog._log_event(
                action_type="security_event",
                category="security",
                description=env._("Security event created: %s", rec.name),
                res_model=self._name,
                res_id=rec.id,
                res_name=rec.name,
                severity="warning" if rec.severity in ("low", "medium") else "critical",
            )
            if rec.severity in ("high", "critical"):
                target_list = ", ".join(rec.service_ids.mapped("name"))
                if rec.server_id:
                    target_list = (
                        f"{rec.server_id.name} / {target_list}"
                        if target_list
                        else rec.server_id.name
                    )
                body_lines = [
                    env._("Type: %s", type_labels.get(rec.event_type, rec.event_type)),
                    env._("Severity: %s", severity_labels.get(rec.severity, rec.severity)),
                ]
                if target_list:
                    body_lines.append(env._("Target: %s", target_list))
                Ntfy.send(
                    title=env._(
                        "SECURITY [%(severity)s]: %(name)s",
                        severity=severity_labels.get(rec.severity, rec.severity).upper(),
                        name=rec.name,
                    ),
                    body="\n".join(body_lines),
                    priority="urgent" if rec.severity == "critical" else "high",
                    tags="lock,rotating_light",
                    click=Ntfy.record_url(rec),
                )
        return records
