# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import api, fields, models, _


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    # Paramètres du journal d'audit
    hosting_audit_log_retention_months = fields.Integer(
        string="Audit log retention (months)",
        config_parameter="hosting.audit_log_retention_months",
        default=24,
        help="Number of months to keep audit log entries. Older entries "
             "are deleted automatically.",
    )

    # Paramètres d'alerte de santé
    hosting_health_alert_email = fields.Char(
        string="Health alert email",
        config_parameter="hosting.health_alert_email",
        help="Email address that receives health check alerts",
    )
    hosting_response_time_threshold_ms = fields.Integer(
        string="Response time threshold (ms)",
        config_parameter="hosting.response_time_threshold_ms",
        default=5000,
        help="Response time threshold in milliseconds. Services "
             "responding slower will trigger a warning.",
    )
    hosting_expiration_warning_days = fields.Integer(
        string="Expiration warning days",
        config_parameter="hosting.expiration_warning_days",
        default=90,
        help="Number of days before expiration to create warning activities",
    )

    # Paramètres de domaines et SSL
    hosting_domain_expiration_warning_days = fields.Integer(
        string="Domain warning days",
        config_parameter="hosting.domain_expiration_warning_days",
        default=60,
        help="Number of days before the domain expires to create warning "
             "activities",
    )
    hosting_ssl_expiration_warning_days = fields.Integer(
        string="SSL warning days",
        config_parameter="hosting.ssl_expiration_warning_days",
        default=30,
        help="Number of days before the SSL certificate expires to create "
             "warning activities",
    )

    # Intégration Cloudflare Registrar
    hosting_cloudflare_email = fields.Char(
        string="Cloudflare email",
        config_parameter="hosting.cloudflare_email",
    )
    hosting_cloudflare_api_key = fields.Char(
        string="Cloudflare global API key",
        config_parameter="hosting.cloudflare_api_key",
    )
    hosting_cloudflare_account_id = fields.Char(
        string="Cloudflare account ID",
        config_parameter="hosting.cloudflare_account_id",
    )
    hosting_last_cloudflare_sync = fields.Datetime(
        string="Last Cloudflare sync",
        config_parameter="hosting.last_cloudflare_sync",
        readonly=True,
    )

    # Jeton API pour rapports de provisioning de tenants
    hosting_provision_api_token = fields.Char(
        string="Tenant provisioning API token",
        config_parameter="hosting.provision_api_token",
        help="Shared token sent by the creation scripts "
             "(create_nextcloud_tenant.sh, create_odoo_client.sh) in the "
             "X-Provision-Token header to /api/hosting/provision/report.",
    )

    # Notifications push ntfy
    hosting_ntfy_url = fields.Char(
        string="ntfy server URL",
        config_parameter="hosting.ntfy_url",
        help="ntfy server URL (e.g. https://ntfy.example.com)",
    )
    hosting_ntfy_token = fields.Char(
        string="ntfy token",
        config_parameter="hosting.ntfy_token",
        help="Authentication token to publish to the ntfy server",
    )
    hosting_ntfy_topic = fields.Char(
        string="ntfy topic",
        config_parameter="hosting.ntfy_topic",
        default="hosting-alerts",
        help="ntfy topic name for hosting alerts",
    )

    # Paramètres de fenêtre de maintenance
    hosting_maintenance_window_enabled = fields.Boolean(
        string="Enable the maintenance window",
        config_parameter="hosting.maintenance_window_enabled",
        default=True,
        help="When enabled, health alerts are suppressed during the "
             "maintenance window",
    )
    hosting_maintenance_start_hour = fields.Float(
        string="Maintenance start hour",
        default=1.5,
        help="Start hour of the maintenance window (24h format, e.g. 1.5 "
             "= 1:30)",
    )
    hosting_maintenance_end_hour = fields.Float(
        string="Maintenance end hour",
        default=2.5,
        help="End hour of the maintenance window (24h format, e.g. 2.5 = 2:30)",
    )
    hosting_maintenance_timezone = fields.Selection(
        selection="_get_timezone_selection",
        string="Maintenance time zone",
        default="America/Toronto",
        help="Time zone for the maintenance window",
    )

    # Paramètres du rapport de sauvegarde
    hosting_backup_report_enabled = fields.Boolean(
        string="Enable the backup report by email",
        config_parameter="hosting.backup_report_enabled",
        default=True,
        help="When enabled, a report is sent by email according to the "
             "mode and schedule configured below.",
    )
    hosting_backup_report_mode = fields.Selection(
        selection=[
            ("scheduled", "Scheduled (daily cron at a fixed hour)"),
            ("immediate", "Immediate (on every run received)"),
        ],
        string="Sending mode",
        config_parameter="hosting.backup_report_mode",
        default="scheduled",
        help="Scheduled: a single email per day at the chosen hour. "
             "Immediate: one email for each report POSTed by the scripts.",
    )
    hosting_backup_report_recipients = fields.Char(
        string="Report recipients",
        config_parameter="hosting.backup_report_recipients",
        help="Comma-separated email addresses. If empty, the email of the "
             "report's company is used.",
    )
    hosting_backup_report_send_hour = fields.Integer(
        string="Sending hour (0-23)",
        config_parameter="hosting.backup_report_send_hour",
        default=6,
        help="Local hour at which the daily report is sent in scheduled mode.",
    )
    hosting_backup_report_timezone = fields.Selection(
        selection="_get_timezone_selection",
        string="Sending time zone",
        config_parameter="hosting.backup_report_timezone",
        default="America/Toronto",
    )
    hosting_backup_report_only_on_issues = fields.Boolean(
        string="Send only when there is a problem",
        config_parameter="hosting.backup_report_only_on_issues",
        default=False,
        help="If enabled, no email is sent for fully successful backups "
             "(ntfy stays active for failures).",
    )

    @api.model
    def _get_timezone_selection(self):
        """Retourner la liste des fuseaux horaires courants pour la sélection.

        Sélection construite par une méthode : Odoo ne traduit pas ses
        libellés, d'où `self.env._` (langue de l'usager qui ouvre le réglage).
        """
        return [
            ("America/Toronto", self.env._("America/Toronto (Eastern)")),
            ("America/Montreal", self.env._("America/Montreal (Eastern)")),
            ("America/New_York", self.env._("America/New_York (Eastern)")),
            ("America/Chicago", self.env._("America/Chicago (Central)")),
            ("America/Denver", self.env._("America/Denver (Mountain)")),
            ("America/Los_Angeles", self.env._("America/Los_Angeles (Pacific)")),
            ("America/Vancouver", self.env._("America/Vancouver (Pacific)")),
            ("UTC", "UTC"),
            ("Europe/London", "Europe/London"),
            ("Europe/Paris", "Europe/Paris"),
        ]

    def set_values(self):
        """Sauvegarder les paramètres de fenêtre de maintenance + propager le
        mode d'envoi du rapport de sauvegarde vers le flag du contrôleur HTTP
        (`hosting.restic_send_email_report`) afin d'éviter les doublons."""
        super().set_values()
        ICP = self.env["ir.config_parameter"].sudo()
        ICP.set_param(
            "hosting.maintenance_start_hour",
            str(self.hosting_maintenance_start_hour),
        )
        ICP.set_param(
            "hosting.maintenance_end_hour",
            str(self.hosting_maintenance_end_hour),
        )
        ICP.set_param(
            "hosting.maintenance_timezone",
            self.hosting_maintenance_timezone or "America/Toronto",
        )
        # Le contrôleur n'envoie le courriel inline qu'en mode immédiat.
        ICP.set_param(
            "hosting.restic_send_email_report",
            "1" if (self.hosting_backup_report_enabled
                    and self.hosting_backup_report_mode == "immediate") else "0",
        )

    @api.model
    def get_values(self):
        """Charger les paramètres de fenêtre de maintenance."""
        res = super().get_values()
        ICP = self.env["ir.config_parameter"].sudo()
        res.update(
            hosting_maintenance_start_hour=float(
                ICP.get_param("hosting.maintenance_start_hour", "1.5")
            ),
            hosting_maintenance_end_hour=float(
                ICP.get_param("hosting.maintenance_end_hour", "2.5")
            ),
            hosting_maintenance_timezone=ICP.get_param(
                "hosting.maintenance_timezone", "America/Toronto"
            ),
        )
        return res

    def action_send_backup_report_test(self):
        """Renvoyer immédiatement le rapport de la dernière exécution de
        sauvegarde (utile pour tester la configuration des destinataires).

        Force temporairement `enabled=True` et `only_on_issues=False` pendant
        l'envoi pour qu'un test reste possible même quand la config courante
        désactiverait le courriel."""
        run = self.env["hosting.backup.run"].search([], order="run_date desc", limit=1)
        if not run:
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("Backup report"),
                    "message": _("No backup run found."),
                    "type": "warning",
                    "sticky": False,
                },
            }
        ICP = self.env["ir.config_parameter"].sudo()
        prev_enabled = ICP.get_param("hosting.backup_report_enabled", "1")
        prev_only_issues = ICP.get_param("hosting.backup_report_only_on_issues", "0")
        ICP.set_param("hosting.backup_report_enabled", "1")
        ICP.set_param("hosting.backup_report_only_on_issues", "0")
        try:
            run.write({"report_sent": False, "report_sent_date": False})
            sent = run.action_send_report()
        finally:
            ICP.set_param("hosting.backup_report_enabled", prev_enabled)
            ICP.set_param("hosting.backup_report_only_on_issues", prev_only_issues)
        if not sent:
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("Backup report"),
                    "message": _(
                        "Sending failed for %(name)s, see the run's chatter.",
                        name=run.name,
                    ),
                    "type": "danger",
                    "sticky": True,
                },
            }
        # Hors de l'appel `_()` du message : l'extracteur de babel (2.10) ne relève
        # pas un `_()` imbriqué dans les arguments d'un autre `_()`, et la chaîne
        # manquait au catalogue.
        to = (self.hosting_backup_report_recipients or run.company_id.email
              or self.env._("(template recipient)"))
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Backup report"),
                "message": _("Report %(name)s sent to %(to)s.", name=run.name, to=to),
                "type": "success",
                "sticky": False,
            },
        }

    # ------------------------------------------------------------------
    # Intégration Action1 (RMM)
    # ------------------------------------------------------------------
    hosting_action1_enabled = fields.Boolean(
        string="Enable Action1 sync",
        config_parameter="hosting.action1_enabled",
        default=False,
        help="Enable the cron that syncs endpoints and groups from the "
             "Action1 console every 6 hours.",
    )
    hosting_action1_api_token = fields.Char(
        string="Action1 API token",
        config_parameter="hosting.action1_api_token",
        help="Action1 API bearer token. Stored encrypted is NOT the "
             "default: use a secret manager to strip it from backups.",
    )
    hosting_action1_api_base_url = fields.Char(
        string="Action1 API base URL",
        config_parameter="hosting.action1_api_base_url",
        default="https://app.action1.com/api/3.0",
        help="Root URL of the Action1 API (no trailing slash).",
    )
    hosting_action1_console_base = fields.Char(
        string="Action1 console base URL",
        config_parameter="hosting.action1_console_base",
        default="https://app.action1.com",
        help="Root URL of the Action1 web console; used to build "
             "clickable links to each endpoint.",
    )
    hosting_action1_conflict_strategy = fields.Selection(
        selection=[
            ("action1_wins",
             "Action1 wins (Action1 overwrites Odoo entries, default)"),
            ("odoo_wins",
             "Odoo wins (the sync only fills empty fields)"),
            ("manual_only",
             "Create only (the sync never writes to existing endpoints)"),
        ],
        string="Action1 conflict resolution",
        config_parameter="hosting.action1_conflict_strategy",
        default="action1_wins",
        help="Default behavior when an endpoint already exists in Odoo "
             "and Action1 sends different values. Can be overridden per "
             "endpoint with the \"Manual entry takes priority\" checkbox.",
    )
    hosting_action1_last_sync = fields.Datetime(
        string="Last Action1 sync",
        config_parameter="hosting.action1_last_sync",
        readonly=True,
    )

    # ------------------------------------------------------------------
    # Intégration VOIP.ms (téléphonie — lecture seule)
    # ------------------------------------------------------------------
    hosting_voipms_enabled = fields.Boolean(
        string="Enable VOIP.ms sync",
        config_parameter="hosting.voipms_enabled",
        default=False,
        help="Enables the crons that sync DID, CDR, transactions and "
             "balance from VOIP.ms (read-only, no writes).",
    )
    hosting_voipms_api_username = fields.Char(
        string="VOIP.ms API user",
        config_parameter="hosting.voipms_api_username",
        help="VOIP.ms account email (e.g. info@example.com). "
             "The API and the IP allowlist are configured at "
             "voip.ms/m/api.php; the Odoo server's outgoing IP must be "
             "listed there.",
    )
    hosting_voipms_api_password = fields.Char(
        string="VOIP.ms API password",
        config_parameter="hosting.voipms_api_password",
        help="API password (separate from the login password), set at "
             "voip.ms/m/api.php.",
    )
    hosting_voipms_low_balance_threshold = fields.Float(
        string="Low balance alert threshold ($)",
        config_parameter="hosting.voipms_low_balance_threshold",
        default=10.0,
        help="Below this balance, a daily ntfy alert is sent.",
    )
    hosting_voipms_cdr_backfill_days = fields.Integer(
        string="CDR window (days)",
        config_parameter="hosting.voipms_cdr_backfill_days",
        default=90,
        help="CDR/transaction history depth fetched at each sync.",
    )
    hosting_voipms_last_sync = fields.Datetime(
        string="Last VOIP.ms sync",
        config_parameter="hosting.voipms_last_sync",
        readonly=True,
    )
    hosting_voipms_last_balance = fields.Char(
        string="Last VOIP.ms balance",
        config_parameter="hosting.voipms_last_balance",
        readonly=True,
    )
    hosting_voipms_last_balance_date = fields.Datetime(
        string="Last balance date",
        config_parameter="hosting.voipms_last_balance_date",
        readonly=True,
    )

    def action_test_voipms_connection(self):
        """Tester la connexion VOIP.ms (getBalance) et afficher le résultat."""
        self.ensure_one()
        try:
            bal = self.env["hosting.voip.did"]._voipms_fetch_balance()
        except Exception as e:  # noqa: BLE001
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("VOIP.ms"),
                    "message": _("Connection failed: %s", e),
                    "type": "danger",
                    "sticky": True,
                },
            }
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("VOIP.ms"),
                "message": _("Connection successful. Balance: %.2f $", bal or 0.0),
                "type": "success",
                "sticky": False,
            },
        }

    def action_sync_voipms_now(self):
        """Lancer la synchro VOIP.ms immédiatement et notifier."""
        self.ensure_one()
        stats = self.env["hosting.voip.did"]._voipms_sync(manual=True)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("VOIP.ms sync"),
                "message": _(
                    "Done: %(d)s DID, %(c)s calls, %(t)s transactions, "
                    "%(e)s error(s).",
                    d=stats["dids"], c=stats["cdr"], t=stats["transactions"],
                    e=stats["errors"],
                ),
                "type": "success" if stats["errors"] == 0 else "warning",
                "sticky": stats["errors"] > 0,
            },
        }

    def action_sync_action1_now(self):
        """Déclencher manuellement la synchro Action1 et notifier."""
        stats = self.env["hosting.endpoint"]._action1_sync(manual=True)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Action1 sync"),
                "message": _(
                    "Done: %(c)s created, %(u)s updated, %(s)s skipped, "
                    "%(e)s error(s).",
                    c=stats["created"],
                    u=stats["updated"],
                    s=stats["skipped"],
                    e=stats["errors"],
                ),
                "type": "success" if stats["errors"] == 0 else "warning",
                "sticky": stats["errors"] > 0,
            },
        }

    def action_sync_cloudflare_domains(self):
        """Lancer la synchronisation Cloudflare manuellement."""
        self.env["hosting.domain"]._cron_sync_cloudflare_domains()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Cloudflare sync"),
                "message": _("Cloudflare domain sync completed."),
                "type": "success",
                "sticky": False,
            },
        }
