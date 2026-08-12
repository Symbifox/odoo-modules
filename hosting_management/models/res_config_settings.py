# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import api, fields, models, _


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    # Paramètres du journal d'audit
    hosting_audit_log_retention_months = fields.Integer(
        string="Rétention du journal d'audit (mois)",
        config_parameter="hosting.audit_log_retention_months",
        default=24,
        help="Nombre de mois de conservation des entrées du journal d'audit. Les entrées plus anciennes sont supprimées automatiquement.",
    )

    # Paramètres d'alerte de santé
    hosting_health_alert_email = fields.Char(
        string="Courriel d'alerte de santé",
        config_parameter="hosting.health_alert_email",
        help="Adresse courriel pour recevoir les alertes de vérification de santé",
    )
    hosting_response_time_threshold_ms = fields.Integer(
        string="Seuil de temps de réponse (ms)",
        config_parameter="hosting.response_time_threshold_ms",
        default=5000,
        help="Seuil de temps de réponse en millisecondes. Les services répondant plus lentement déclencheront un avertissement.",
    )
    hosting_expiration_warning_days = fields.Integer(
        string="Jours d'avertissement d'expiration",
        config_parameter="hosting.expiration_warning_days",
        default=90,
        help="Nombre de jours avant l'expiration pour créer des activités d'avertissement",
    )

    # Paramètres de domaines et SSL
    hosting_domain_expiration_warning_days = fields.Integer(
        string="Jours d'avertissement de domaine",
        config_parameter="hosting.domain_expiration_warning_days",
        default=60,
        help="Nombre de jours avant l'expiration du domaine pour créer des activités d'avertissement",
    )
    hosting_ssl_expiration_warning_days = fields.Integer(
        string="Jours d'avertissement SSL",
        config_parameter="hosting.ssl_expiration_warning_days",
        default=30,
        help="Nombre de jours avant l'expiration du certificat SSL pour créer des activités d'avertissement",
    )

    # Intégration Cloudflare Registrar
    hosting_cloudflare_email = fields.Char(
        string="Courriel Cloudflare",
        config_parameter="hosting.cloudflare_email",
    )
    hosting_cloudflare_api_key = fields.Char(
        string="Clé API globale Cloudflare",
        config_parameter="hosting.cloudflare_api_key",
    )
    hosting_cloudflare_account_id = fields.Char(
        string="ID de compte Cloudflare",
        config_parameter="hosting.cloudflare_account_id",
    )
    hosting_last_cloudflare_sync = fields.Datetime(
        string="Dernière synchronisation Cloudflare",
        config_parameter="hosting.last_cloudflare_sync",
        readonly=True,
    )

    # Jeton API pour rapports de provisioning de tenants
    hosting_provision_api_token = fields.Char(
        string="Jeton API provisioning de tenants",
        config_parameter="hosting.provision_api_token",
        help="Jeton partagé envoyé par les scripts de création (create_nextcloud_tenant.sh, "
             "create_odoo_client.sh) dans l'entête X-Provision-Token vers "
             "/api/hosting/provision/report.",
    )

    # Notifications push ntfy
    hosting_ntfy_url = fields.Char(
        string="URL du serveur ntfy",
        config_parameter="hosting.ntfy_url",
        help="URL du serveur ntfy (ex. : https://ntfy.example.com)",
    )
    hosting_ntfy_token = fields.Char(
        string="Jeton ntfy",
        config_parameter="hosting.ntfy_token",
        help="Jeton d'authentification pour publier sur le serveur ntfy",
    )
    hosting_ntfy_topic = fields.Char(
        string="Sujet ntfy",
        config_parameter="hosting.ntfy_topic",
        default="hosting-alerts",
        help="Nom du sujet ntfy pour les alertes d'hébergement",
    )

    # Paramètres de fenêtre de maintenance
    hosting_maintenance_window_enabled = fields.Boolean(
        string="Activer la fenêtre de maintenance",
        config_parameter="hosting.maintenance_window_enabled",
        default=True,
        help="Lorsqu'activé, les alertes de santé sont supprimées pendant la fenêtre de maintenance",
    )
    hosting_maintenance_start_hour = fields.Float(
        string="Heure de début de maintenance",
        default=1.5,
        help="Heure de début de la fenêtre de maintenance (format 24h, ex. : 1.5 = 1h30)",
    )
    hosting_maintenance_end_hour = fields.Float(
        string="Heure de fin de maintenance",
        default=2.5,
        help="Heure de fin de la fenêtre de maintenance (format 24h, ex. : 2.5 = 2h30)",
    )
    hosting_maintenance_timezone = fields.Selection(
        selection="_get_timezone_selection",
        string="Fuseau horaire de maintenance",
        default="America/Toronto",
        help="Fuseau horaire pour la fenêtre de maintenance",
    )

    # Paramètres du rapport de sauvegarde
    hosting_backup_report_enabled = fields.Boolean(
        string="Activer le rapport de sauvegarde par courriel",
        config_parameter="hosting.backup_report_enabled",
        default=True,
        help="Lorsqu'activé, un rapport est envoyé par courriel selon le mode et "
             "l'horaire configurés ci-dessous.",
    )
    hosting_backup_report_mode = fields.Selection(
        selection=[
            ("scheduled", "Planifié (cron quotidien à heure fixe)"),
            ("immediate", "Immédiat (à chaque exécution reçue)"),
        ],
        string="Mode d'envoi",
        config_parameter="hosting.backup_report_mode",
        default="scheduled",
        help="Planifié : un seul courriel par jour à l'heure choisie. "
             "Immédiat : un courriel à chaque rapport POSTé par les scripts.",
    )
    hosting_backup_report_recipients = fields.Char(
        string="Destinataires du rapport",
        config_parameter="hosting.backup_report_recipients",
        help="Adresses courriel séparées par des virgules. "
             "Si vide, le courriel de la société du rapport est utilisé.",
    )
    hosting_backup_report_send_hour = fields.Integer(
        string="Heure d'envoi (0-23)",
        config_parameter="hosting.backup_report_send_hour",
        default=6,
        help="Heure locale à laquelle le rapport quotidien est envoyé en mode planifié.",
    )
    hosting_backup_report_timezone = fields.Selection(
        selection="_get_timezone_selection",
        string="Fuseau horaire d'envoi",
        config_parameter="hosting.backup_report_timezone",
        default="America/Toronto",
    )
    hosting_backup_report_only_on_issues = fields.Boolean(
        string="Envoyer seulement en cas de problème",
        config_parameter="hosting.backup_report_only_on_issues",
        default=False,
        help="Si activé, aucun courriel n'est envoyé pour les sauvegardes "
             "entièrement réussies (ntfy reste actif pour les échecs).",
    )

    @api.model
    def _get_timezone_selection(self):
        """Retourner la liste des fuseaux horaires courants pour la sélection."""
        return [
            ("America/Toronto", "America/Toronto (Est)"),
            ("America/Montreal", "America/Montreal (Est)"),
            ("America/New_York", "America/New_York (Est)"),
            ("America/Chicago", "America/Chicago (Centre)"),
            ("America/Denver", "America/Denver (Rocheuses)"),
            ("America/Los_Angeles", "America/Los_Angeles (Pacifique)"),
            ("America/Vancouver", "America/Vancouver (Pacifique)"),
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
                    "title": _("Rapport de sauvegarde"),
                    "message": _("Aucune exécution de sauvegarde trouvée."),
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
                    "title": _("Rapport de sauvegarde"),
                    "message": _(
                        "Échec de l'envoi pour %(name)s — voir le chatter de l'exécution.",
                        name=run.name,
                    ),
                    "type": "danger",
                    "sticky": True,
                },
            }
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Rapport de sauvegarde"),
                "message": _(
                    "Rapport %(name)s envoyé à %(to)s.",
                    name=run.name,
                    to=self.hosting_backup_report_recipients
                    or (run.company_id.email or "(destinataire du gabarit)"),
                ),
                "type": "success",
                "sticky": False,
            },
        }

    # ------------------------------------------------------------------
    # Intégration Action1 (RMM)
    # ------------------------------------------------------------------
    hosting_action1_enabled = fields.Boolean(
        string="Activer la synchronisation Action1",
        config_parameter="hosting.action1_enabled",
        default=False,
        help="Activer le cron qui synchronise les endpoints et groupes depuis "
        "la console Action1 toutes les 6 heures.",
    )
    hosting_action1_api_token = fields.Char(
        string="Jeton API Action1",
        config_parameter="hosting.action1_api_token",
        help="Bearer token API Action1. Stocké chiffré n'est PAS la valeur par "
        "défaut — utiliser un secret manager pour l'expurger en backup.",
    )
    hosting_action1_api_base_url = fields.Char(
        string="URL de base API Action1",
        config_parameter="hosting.action1_api_base_url",
        default="https://app.action1.com/api/3.0",
        help="URL racine de l'API Action1 (sans slash final).",
    )
    hosting_action1_console_base = fields.Char(
        string="URL de base console Action1",
        config_parameter="hosting.action1_console_base",
        default="https://app.action1.com",
        help="URL racine de la console web Action1 ; sert à construire les liens "
        "cliquables vers chaque endpoint.",
    )
    hosting_action1_conflict_strategy = fields.Selection(
        selection=[
            ("action1_wins",
             "Action1 gagne (Action1 écrase la saisie Odoo — défaut)"),
            ("odoo_wins",
             "Odoo gagne (la sync ne remplit que les champs vides)"),
            ("manual_only",
             "Création uniquement (la sync n'écrit jamais sur les postes existants)"),
        ],
        string="Résolution de conflits Action1",
        config_parameter="hosting.action1_conflict_strategy",
        default="action1_wins",
        help="Comportement par défaut quand un poste existe déjà dans Odoo et "
        "qu'Action1 envoie des valeurs différentes. Peut être surchargé par "
        "poste via la case « Saisie manuelle prioritaire ».",
    )
    hosting_action1_last_sync = fields.Datetime(
        string="Dernière synchro Action1",
        config_parameter="hosting.action1_last_sync",
        readonly=True,
    )

    # ------------------------------------------------------------------
    # Intégration VOIP.ms (téléphonie — lecture seule)
    # ------------------------------------------------------------------
    hosting_voipms_enabled = fields.Boolean(
        string="Activer la synchronisation VOIP.ms",
        config_parameter="hosting.voipms_enabled",
        default=False,
        help="Active les crons qui synchronisent DID, CDR, transactions et solde "
        "depuis VOIP.ms (lecture seule, aucune écriture).",
    )
    hosting_voipms_api_username = fields.Char(
        string="Utilisateur API VOIP.ms",
        config_parameter="hosting.voipms_api_username",
        help="Courriel du compte VOIP.ms (ex. info@example.com). "
        "L'API et l'allowlist IP se configurent sur voip.ms/m/api.php — "
        "l'IP de sortie du serveur Odoo doit y figurer.",
    )
    hosting_voipms_api_password = fields.Char(
        string="Mot de passe API VOIP.ms",
        config_parameter="hosting.voipms_api_password",
        help="Mot de passe API (distinct du mot de passe de connexion), "
        "défini sur voip.ms/m/api.php.",
    )
    hosting_voipms_low_balance_threshold = fields.Float(
        string="Seuil d'alerte de solde bas ($)",
        config_parameter="hosting.voipms_low_balance_threshold",
        default=10.0,
        help="Sous ce solde, une alerte ntfy quotidienne est émise.",
    )
    hosting_voipms_cdr_backfill_days = fields.Integer(
        string="Fenêtre CDR (jours)",
        config_parameter="hosting.voipms_cdr_backfill_days",
        default=90,
        help="Profondeur d'historique CDR/transactions récupérée à chaque synchro.",
    )
    hosting_voipms_last_sync = fields.Datetime(
        string="Dernière synchro VOIP.ms",
        config_parameter="hosting.voipms_last_sync",
        readonly=True,
    )
    hosting_voipms_last_balance = fields.Char(
        string="Dernier solde VOIP.ms",
        config_parameter="hosting.voipms_last_balance",
        readonly=True,
    )
    hosting_voipms_last_balance_date = fields.Datetime(
        string="Date du dernier solde",
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
                    "message": _("Échec de connexion : %s", e),
                    "type": "danger",
                    "sticky": True,
                },
            }
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("VOIP.ms"),
                "message": _("Connexion réussie. Solde : %.2f $", bal or 0.0),
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
                "title": _("Synchronisation VOIP.ms"),
                "message": _(
                    "Terminée : %(d)s DID, %(c)s appels, %(t)s transactions, "
                    "%(e)s erreur(s).",
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
                "title": _("Synchronisation Action1"),
                "message": _(
                    "Terminée : %(c)s créés, %(u)s mis à jour, %(s)s ignorés, "
                    "%(e)s erreur(s).",
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
                "title": _("Synchronisation Cloudflare"),
                "message": _("Synchronisation des domaines Cloudflare terminée."),
                "type": "success",
                "sticky": False,
            },
        }
