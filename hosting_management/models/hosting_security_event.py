# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import api, fields, models


class HostingSecurityEvent(models.Model):
    _name = "hosting.security.event"
    _description = "Événement de sécurité d'hébergement"
    _order = "event_date desc, id desc"
    _inherit = ["mail.thread", "mail.activity.mixin"]

    name = fields.Char(
        string="Titre",
        required=True,
        tracking=True,
    )
    event_date = fields.Datetime(
        string="Date de l'événement",
        required=True,
        default=fields.Datetime.now,
        tracking=True,
    )
    event_type = fields.Selection(
        selection=[
            ("unauthorized_access", "Accès non autorisé"),
            ("certificate_issue", "Problème de certificat"),
            ("vulnerability", "Vulnérabilité"),
            ("data_breach", "Fuite de données"),
            ("service_compromise", "Compromission de service"),
            ("credential_leak", "Fuite d'identifiants"),
            ("dns_issue", "Problème DNS"),
            ("backup_failure", "Échec de sauvegarde"),
            ("other", "Autre"),
        ],
        string="Type",
        required=True,
        default="other",
        tracking=True,
    )
    severity = fields.Selection(
        selection=[
            ("low", "Faible"),
            ("medium", "Moyen"),
            ("high", "Élevé"),
            ("critical", "Critique"),
        ],
        string="Sévérité",
        required=True,
        default="medium",
        tracking=True,
    )
    state = fields.Selection(
        selection=[
            ("open", "Ouvert"),
            ("investigating", "En investigation"),
            ("mitigated", "Atténué"),
            ("resolved", "Résolu"),
            ("false_positive", "Faux positif"),
        ],
        string="État",
        required=True,
        default="open",
        tracking=True,
    )

    # -- Relations --
    service_ids = fields.Many2many(
        comodel_name="hosting.service",
        string="Services affectés",
    )
    server_id = fields.Many2one(
        comodel_name="hosting.server",
        string="Serveur affecté",
    )

    # -- Details --
    description = fields.Html(
        string="Détails de l'incident",
    )
    impact = fields.Html(
        string="Évaluation de l'impact",
    )
    resolution = fields.Html(
        string="Notes de résolution",
    )
    resolved_date = fields.Datetime(
        string="Date de résolution",
        tracking=True,
    )

    # -- Assignment --
    assigned_user_id = fields.Many2one(
        comodel_name="res.users",
        string="Assigné à",
        tracking=True,
    )
    reported_by_id = fields.Many2one(
        comodel_name="res.users",
        string="Signalé par",
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
        type_labels = dict(self._fields["event_type"].selection)
        severity_labels = dict(self._fields["severity"].selection)
        for rec in records:
            AuditLog._log_event(
                action_type="security_event",
                category="security",
                description=f"Événement de sécurité créé : {rec.name}",
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
                    f"Type : {type_labels.get(rec.event_type, rec.event_type)}",
                    f"Sévérité : {severity_labels.get(rec.severity, rec.severity)}",
                ]
                if target_list:
                    body_lines.append(f"Cible : {target_list}")
                Ntfy.send(
                    title=f"SÉCURITÉ [{severity_labels.get(rec.severity, rec.severity).upper()}] : {rec.name}",
                    body="\n".join(body_lines),
                    priority="urgent" if rec.severity == "critical" else "high",
                    tags="lock,rotating_light",
                    click=Ntfy.record_url(rec),
                )
        return records
