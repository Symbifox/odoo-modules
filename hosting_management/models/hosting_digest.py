# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import logging
from datetime import timedelta

from markupsafe import escape as _esc

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class HostingDigest(models.Model):
    _name = "hosting.digest"
    _description = "Configuration du récapitulatif d'hébergement"
    _order = "name"

    name = fields.Char(
        string="Nom",
        required=True,
        default="Récapitulatif hebdomadaire",
    )
    user_ids = fields.Many2many(
        comodel_name="res.users",
        relation="hosting_digest_user_rel",
        column1="digest_id",
        column2="user_id",
        string="Destinataires",
        required=True,
    )
    frequency = fields.Selection(
        selection=[
            ("daily", "Quotidien"),
            ("weekly", "Hebdomadaire"),
            ("monthly", "Mensuel"),
        ],
        string="Fréquence",
        default="weekly",
        required=True,
    )

    # Contenu à inclure
    include_expiring = fields.Boolean(
        string="Inclure les services expirant",
        default=True,
    )
    expiring_days = fields.Integer(
        string="Expire dans (jours)",
        default=90,
        help="Inclure les services expirant dans ce nombre de jours",
    )
    include_updates = fields.Boolean(
        string="Inclure les mises à jour disponibles",
        default=True,
    )
    include_storage_alerts = fields.Boolean(
        string="Inclure les alertes de stockage",
        default=True,
    )
    include_health_issues = fields.Boolean(
        string="Inclure les problèmes de santé",
        default=True,
    )
    include_maintenance_due = fields.Boolean(
        string="Inclure la maintenance prévue",
        default=True,
        help="Inclure les tâches de maintenance à venir et en retard dans le récapitulatif",
    )
    maintenance_warning_days = fields.Integer(
        string="Jours d'avertissement de maintenance",
        default=14,
        help="Inclure les tâches de maintenance dues dans ce nombre de jours",
    )

    last_sent = fields.Datetime(
        string="Dernier envoi",
        readonly=True,
    )
    active = fields.Boolean(
        string="Actif",
        default=True,
    )

    @api.model
    def _cron_send_digests(self):
        """Envoyer les courriels récapitulatifs selon la fréquence configurée."""
        today = fields.Date.today()
        now = fields.Datetime.now()

        digests = self.search([("active", "=", True)])

        for digest in digests:
            should_send = False

            if not digest.last_sent:
                should_send = True
            else:
                last_sent_date = digest.last_sent.date()
                if digest.frequency == "daily":
                    should_send = last_sent_date < today
                elif digest.frequency == "weekly":
                    days_since = (today - last_sent_date).days
                    should_send = days_since >= 7
                elif digest.frequency == "monthly":
                    days_since = (today - last_sent_date).days
                    should_send = (
                        days_since >= 30
                        or today.month != last_sent_date.month
                    )

            if should_send:
                try:
                    digest._send_digest()
                    digest.last_sent = now
                except Exception:
                    _logger.exception("Failed to send digest %s", digest.name)

    def _generate_digest_data(self):
        """Rassembler les données pour le courriel récapitulatif."""
        self.ensure_one()
        today = fields.Date.today()
        Service = self.env["hosting.service"]
        MaintenanceSchedule = self.env["hosting.maintenance.schedule"]

        data = {
            "digest": self,
            "expiring_services": [],
            "updates_services": [],
            "storage_alert_services": [],
            "health_issue_services": [],
            "maintenance_overdue": [],
            "maintenance_due_soon": [],
        }

        active_services = Service.search([("state", "=", "active")])

        if self.include_expiring:
            warning_date = today + timedelta(days=self.expiring_days)
            data["expiring_services"] = active_services.filtered(
                lambda s: s.date_expiration
                and today < s.date_expiration <= warning_date
            ).sorted(key=lambda s: s.date_expiration)

        if self.include_updates:
            data["updates_services"] = active_services.filtered(
                lambda s: s.update_available
            )

        if self.include_storage_alerts:
            data["storage_alert_services"] = active_services.filtered(
                lambda s: s.storage_alert
            )

        if self.include_health_issues:
            data["health_issue_services"] = active_services.filtered(
                lambda s: hasattr(s, "is_service_up")
                and s.is_service_up is False
            )

        if self.include_maintenance_due:
            warning_date = today + timedelta(days=self.maintenance_warning_days)
            data["maintenance_overdue"] = MaintenanceSchedule.search([
                ("active", "=", True),
                ("next_due", "<", today),
                ("service_id.state", "=", "active"),
            ], order="next_due, service_id")

            data["maintenance_due_soon"] = MaintenanceSchedule.search([
                ("active", "=", True),
                ("next_due", ">=", today),
                ("next_due", "<=", warning_date),
                ("service_id.state", "=", "active"),
            ], order="next_due, service_id")

        return data

    def _generate_digest_html(self, data, recipient):
        """Générer le corps HTML pour le courriel récapitulatif."""
        from . import hosting_email_template as email_tpl

        content_parts = []

        # Salutation
        frequency_text = {
            "daily": "quotidien",
            "weekly": "hebdomadaire",
            "monthly": "mensuel",
        }.get(self.frequency, "")

        content_parts.append(
            f'<p style="font-family:\'Lexend\',\'Segoe UI\',Arial,sans-serif; font-size:16px; line-height:26px; color:#374151; margin:0 0 8px 0;">'
            f"Bonjour {recipient.name},</p>"
        )
        content_parts.append(
            f'<p style="font-family:\'Lexend\',\'Segoe UI\',Arial,sans-serif; font-size:16px; line-height:26px; color:#374151; margin:0 0 20px 0;">'
            f"Voici votre résumé <strong>{frequency_text}</strong> des services d'hébergement.</p>"
        )

        # Cartes de résumé
        summary_items = []
        if data["expiring_services"]:
            summary_items.append(("Expirant bientôt", str(len(data["expiring_services"]))))
        if data["updates_services"]:
            summary_items.append(("Mises à jour disponibles", str(len(data["updates_services"]))))
        if data["storage_alert_services"]:
            summary_items.append(("Alertes de stockage", str(len(data["storage_alert_services"]))))
        if data["health_issue_services"]:
            summary_items.append(("Problèmes de santé", str(len(data["health_issue_services"]))))
        if data["maintenance_overdue"]:
            summary_items.append(("Maintenance en retard", str(len(data["maintenance_overdue"]))))
        if data["maintenance_due_soon"]:
            summary_items.append(("Maintenance à venir", str(len(data["maintenance_due_soon"]))))

        if summary_items:
            content_parts.append(email_tpl.get_info_card(summary_items, "Résumé", company=self.env.company))

        # Section Services expirant
        if data["expiring_services"]:
            content_parts.append(email_tpl.get_section_title("Services expirant bientôt", "#dc3545"))
            rows = []
            for service in data["expiring_services"]:
                days = service.days_until_expiration
                days_style = "color:#dc3545; font-weight:600;" if days <= 30 else "color:#ffc107; font-weight:600;"
                rows.append([
                    f"{_esc(service.name)}<br/><span style='color:#6B7280; font-size:12px;'>{_esc(service.code)}</span>",
                    _esc(service.partner_id.name or ""),
                    str(service.date_expiration),
                    f"<span style='{days_style}'>{days} jours</span>",
                ])
            content_parts.append(email_tpl.get_data_table(
                ["Service", "Client", "Expiration", "Jours restants"],
                rows,
                header_bg="#dc3545"
            ))

        # Section Mises à jour disponibles
        if data["updates_services"]:
            content_parts.append(email_tpl.get_section_title(
                "Mises à jour disponibles",
                self.env.company.report_brand_primary or "#714B67",
            ))
            rows = []
            for service in data["updates_services"]:
                software_name = _esc(service.software_id.name) if service.software_id else "N/D"
                current_ver = _esc(service.installed_version_id.version) if service.installed_version_id else "N/D"
                latest_ver = _esc(service.software_id.latest_version) if service.software_id else "N/D"
                rows.append([
                    f"{_esc(service.name)}<br/><span style='color:#6B7280; font-size:12px;'>{_esc(service.code)}</span>",
                    _esc(service.partner_id.name or ""),
                    software_name,
                    f"{current_ver} → <span style='color:#198754; font-weight:600;'>{latest_ver}</span>",
                ])
            content_parts.append(email_tpl.get_data_table(
                ["Service", "Client", "Logiciel", "Version"],
                rows,
                header_bg=self.env.company.report_brand_primary or "#714B67",
            ))

        # Section Alertes de stockage
        if data["storage_alert_services"]:
            content_parts.append(email_tpl.get_section_title("Alertes de stockage", "#ffc107"))
            rows = []
            for service in data["storage_alert_services"]:
                usage = service.storage_used_percent
                usage_style = "color:#dc3545; font-weight:600;" if usage >= 90 else "color:#ffc107; font-weight:600;"
                rows.append([
                    f"{_esc(service.name)}<br/><span style='color:#6B7280; font-size:12px;'>{_esc(service.code)}</span>",
                    _esc(service.partner_id.name or ""),
                    f"<span style='{usage_style}'>{usage:.1f}%</span>",
                ])
            content_parts.append(email_tpl.get_data_table(
                ["Service", "Client", "Utilisation"],
                rows,
                header_bg="#ffc107",
                header_color=self.env.company.report_brand_dark or "#212529",
            ))

        # Section Problèmes de santé
        if data["health_issue_services"]:
            content_parts.append(email_tpl.get_section_title("Problèmes de santé", "#dc3545"))
            rows = []
            for service in data["health_issue_services"]:
                status = service.last_health_status or "Inconnu"
                status_badge = email_tpl.get_status_badge(status, "small")
                rows.append([
                    f"{_esc(service.name)}<br/><span style='color:#6B7280; font-size:12px;'>{_esc(service.code)}</span>",
                    _esc(service.partner_id.name or ""),
                    status_badge,
                ])
            content_parts.append(email_tpl.get_data_table(
                ["Service", "Client", "État"],
                rows,
                header_bg="#dc3545"
            ))

        # Section Maintenance en retard
        if data["maintenance_overdue"]:
            content_parts.append(email_tpl.get_section_title("Maintenance en retard", "#dc3545"))
            rows = []
            for schedule in data["maintenance_overdue"]:
                # days_until_due est négatif pour une tâche échue
                days = abs(schedule.days_until_due)
                days_style = "color:#dc3545; font-weight:600;"
                maint_type = schedule._get_type_display()
                assigned = _esc(schedule.user_id.name) if schedule.user_id else "Non assigné"
                rows.append([
                    f"{_esc(schedule.service_id.name)}<br/><span style='color:#6B7280; font-size:12px;'>{_esc(schedule.service_id.code)}</span>",
                    f"<span style='font-weight:600;'>{_esc(schedule.name)}</span><br/><span style='color:#6B7280; font-size:12px;'>{_esc(maint_type)}</span>",
                    str(schedule.next_due),
                    f"<span style='{days_style}'>{days} j de retard</span>",
                    assigned,
                ])
            content_parts.append(email_tpl.get_data_table(
                ["Service", "Tâche de maintenance", "Date d'échéance", "Jours", "Assigné à"],
                rows,
                header_bg="#dc3545"
            ))

        # Section Maintenance à venir
        if data["maintenance_due_soon"]:
            content_parts.append(email_tpl.get_section_title("Maintenance à venir", "#ffc107"))
            rows = []
            for schedule in data["maintenance_due_soon"]:
                days = schedule.days_until_due
                if days <= 7:
                    days_style = "color:#ffc107; font-weight:600;"
                else:
                    days_style = "color:#6B7280;"
                maint_type = schedule._get_type_display()
                assigned = _esc(schedule.user_id.name) if schedule.user_id else "Non assigné"
                rows.append([
                    f"{_esc(schedule.service_id.name)}<br/><span style='color:#6B7280; font-size:12px;'>{_esc(schedule.service_id.code)}</span>",
                    f"<span style='font-weight:600;'>{_esc(schedule.name)}</span><br/><span style='color:#6B7280; font-size:12px;'>{_esc(maint_type)}</span>",
                    str(schedule.next_due),
                    f"<span style='{days_style}'>{days} jours</span>",
                    assigned,
                ])
            content_parts.append(email_tpl.get_data_table(
                ["Service", "Tâche de maintenance", "Date d'échéance", "Jours", "Assigné à"],
                rows,
                header_bg="#ffc107",
                header_color=self.env.company.report_brand_dark or "#212529",
            ))

        # Pied de page de contact
        content_parts.append(email_tpl.get_contact_footer())

        content = "".join(content_parts)
        return email_tpl.get_email_wrapper("Résumé des services", content, company=self.env.company)

    def _send_digest(self):
        """Envoyer le courriel récapitulatif à tous les destinataires."""
        self.ensure_one()
        data = self._generate_digest_data()

        # Ignorer s'il n'y a rien à signaler
        has_data = any([
            data["expiring_services"],
            data["updates_services"],
            data["storage_alert_services"],
            data["health_issue_services"],
            data["maintenance_overdue"],
            data["maintenance_due_soon"],
        ])

        if not has_data:
            _logger.info("Skipping digest %s - no data to report", self.name)
            return

        for user in self.user_ids:
            if not user.email:
                _logger.warning("User %s has no email, skipping", user.name)
                continue

            # Générer le corps HTML personnalisé
            body_html = self._generate_digest_html(data, user)

            # Envoyer le courriel directement
            mail_values = {
                "subject": f"Résumé des services d'hébergement - {self.name}",
                "email_from": self.create_uid.email_formatted or self.env.user.email_formatted,
                "email_to": user.email,
                "body_html": body_html,
                "auto_delete": True,
            }
            mail = self.env["mail.mail"].sudo().create(mail_values)
            mail.send()

    def action_send_now(self):
        """Bouton d'envoi manuel pour tester."""
        self.ensure_one()
        self._send_digest()
        self.last_sent = fields.Datetime.now()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "Récapitulatif envoyé",
                "message": f"Récapitulatif envoyé à {len(self.user_ids)} destinataire(s).",
                "type": "success",
            },
        }

    def action_preview_digest(self):
        """Aperçu des données du récapitulatif."""
        self.ensure_one()
        data = self._generate_digest_data()

        message_parts = []
        if data["expiring_services"]:
            message_parts.append(
                f"Expirant : {len(data['expiring_services'])} services"
            )
        if data["updates_services"]:
            message_parts.append(
                f"Mises à jour : {len(data['updates_services'])} services"
            )
        if data["storage_alert_services"]:
            message_parts.append(
                f"Alertes de stockage : {len(data['storage_alert_services'])} services"
            )
        if data["health_issue_services"]:
            message_parts.append(
                f"Problèmes de santé : {len(data['health_issue_services'])} services"
            )
        if data["maintenance_overdue"]:
            message_parts.append(
                f"Maintenance en retard : {len(data['maintenance_overdue'])} tâches"
            )
        if data["maintenance_due_soon"]:
            message_parts.append(
                f"Maintenance à venir : {len(data['maintenance_due_soon'])} tâches"
            )

        if not message_parts:
            message = "Aucun élément à signaler dans ce récapitulatif."
        else:
            message = "\n".join(message_parts)

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "Aperçu du récapitulatif",
                "message": message,
                "type": "info",
                "sticky": True,
            },
        }
