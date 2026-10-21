# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import logging
from datetime import timedelta

from markupsafe import escape as _esc

from odoo import api, fields, models

from .hosting_lang import reader_lang

_logger = logging.getLogger(__name__)


class HostingDigest(models.Model):
    _name = "hosting.digest"
    _description = "Hosting digest configuration"
    _order = "name"

    name = fields.Char(
        string="Name",
        required=True,
        default=lambda self: self.env._("Weekly digest"),
    )
    user_ids = fields.Many2many(
        comodel_name="res.users",
        relation="hosting_digest_user_rel",
        column1="digest_id",
        column2="user_id",
        string="Recipients",
        required=True,
    )
    frequency = fields.Selection(
        selection=[
            ("daily", "Daily"),
            ("weekly", "Weekly"),
            ("monthly", "Monthly"),
        ],
        string="Frequency",
        default="weekly",
        required=True,
    )

    # Contenu à inclure
    include_expiring = fields.Boolean(
        string="Include expiring services",
        default=True,
    )
    expiring_days = fields.Integer(
        string="Expires in (days)",
        default=90,
        help="Include services expiring within this number of days",
    )
    include_updates = fields.Boolean(
        string="Include available updates",
        default=True,
    )
    include_storage_alerts = fields.Boolean(
        string="Include storage alerts",
        default=True,
    )
    include_health_issues = fields.Boolean(
        string="Include health issues",
        default=True,
    )
    include_maintenance_due = fields.Boolean(
        string="Include scheduled maintenance",
        default=True,
        help="Include upcoming and overdue maintenance tasks in the digest",
    )
    maintenance_warning_days = fields.Integer(
        string="Maintenance warning days",
        default=14,
        help="Include maintenance tasks due within this number of days",
    )

    last_sent = fields.Datetime(
        string="Last sent",
        readonly=True,
    )
    active = fields.Boolean(
        string="Active",
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
                *MaintenanceSchedule._target_active_domain(),
            ], order="next_due, service_id")

            data["maintenance_due_soon"] = MaintenanceSchedule.search([
                ("active", "=", True),
                ("next_due", ">=", today),
                ("next_due", "<=", warning_date),
                *MaintenanceSchedule._target_active_domain(),
            ], order="next_due, service_id")

        return data

    def _digest_lang(self, recipient):
        """Langue du lecteur du récapitulatif : celle de l'usager destinataire,
        sinon celle de la société, sinon en_US ; seulement une langue INSTALLÉE
        (une langue désactivée ferait lever `env.lang` au rendu)."""
        installed = dict(self.env["res.lang"].get_installed())
        if recipient and recipient.lang in installed:
            return recipient.lang
        return reader_lang(self.env)

    def _generate_digest_html(self, data, recipient):
        """Générer le corps HTML pour le courriel récapitulatif.

        Écrit dans la langue du destinataire (usager opérateur) : `self` est
        épinglé à sa langue, et chaque texte passe par `digest.env._` (le cadre
        du travail planifié n'a pas la langue du lecteur, pièges 35/60).
        """
        from . import hosting_email_template as email_tpl

        digest = self.with_context(lang=self._digest_lang(recipient))
        content_parts = []
        societe = digest.env.company
        police = email_tpl._brand_font(societe)
        danger, avert, succes = (email_tpl.ETATS[k] for k in ("danger", "avertissement", "succes"))

        # Salutation
        intro = {
            "daily": digest.env._("Here is your <strong>daily</strong> summary of hosting services."),
            "weekly": digest.env._("Here is your <strong>weekly</strong> summary of hosting services."),
            "monthly": digest.env._("Here is your <strong>monthly</strong> summary of hosting services."),
        }.get(self.frequency) or digest.env._("Here is your summary of hosting services.")
        bonjour = digest.env._("Hello %(name)s,", name=_esc(recipient.name or ""))

        content_parts.append(
            f'<p style="font-family:{police}; font-size:16px; line-height:26px; color:#374151; margin:0 0 8px 0;">'
            f"{bonjour}</p>"
        )
        content_parts.append(
            f'<p style="font-family:{police}; font-size:16px; line-height:26px; color:#374151; margin:0 0 20px 0;">'
            f"{intro}</p>"
        )

        # Cartes de résumé
        summary_items = []
        if data["expiring_services"]:
            summary_items.append((digest.env._("Expiring soon"), str(len(data["expiring_services"]))))
        if data["updates_services"]:
            summary_items.append((digest.env._("Available updates"), str(len(data["updates_services"]))))
        if data["storage_alert_services"]:
            summary_items.append((digest.env._("Storage alerts"), str(len(data["storage_alert_services"]))))
        if data["health_issue_services"]:
            summary_items.append((digest.env._("Health issues"), str(len(data["health_issue_services"]))))
        if data["maintenance_overdue"]:
            summary_items.append((digest.env._("Overdue maintenance"), str(len(data["maintenance_overdue"]))))
        if data["maintenance_due_soon"]:
            summary_items.append((digest.env._("Upcoming maintenance"), str(len(data["maintenance_due_soon"]))))

        if summary_items:
            content_parts.append(email_tpl.get_info_card(summary_items, digest.env._("Summary"), company=societe))

        # Section Services expirant
        if data["expiring_services"]:
            content_parts.append(email_tpl.get_section_title(digest.env._("Services expiring soon"), danger["texte"], company=societe))
            rows = []
            for service in data["expiring_services"]:
                days = service.days_until_expiration
                days_style = f"color:{danger['texte']}; font-weight:600;" if days <= 30 else f"color:{avert['texte']}; font-weight:600;"
                jours = _esc(digest.env._("%(n)s days", n=days))
                rows.append([
                    f"{_esc(service.name)}<br/><span style='color:#6B7280; font-size:12px;'>{_esc(service.code)}</span>",
                    _esc(service.partner_id.name or ""),
                    str(service.date_expiration),
                    f"<span style='{days_style}'>{jours}</span>",
                ])
            content_parts.append(email_tpl.get_data_table(
                [digest.env._("Service"), digest.env._("Client"), digest.env._("Expiration"), digest.env._("Days left")],
                rows,
                header_bg=danger["entete"], header_color=danger["entete_texte"], company=societe,
            ))

        # Section Mises à jour disponibles
        if data["updates_services"]:
            content_parts.append(email_tpl.get_section_title(
                digest.env._("Available updates"), company=societe))
            rows = []
            nd = _esc(digest.env._("N/A"))
            for service in data["updates_services"]:
                software_name = _esc(service.software_id.name) if service.software_id else nd
                current_ver = _esc(service.installed_version_id.version) if service.installed_version_id else nd
                latest_ver = _esc(service.software_id.latest_version) if service.software_id else nd
                rows.append([
                    f"{_esc(service.name)}<br/><span style='color:#6B7280; font-size:12px;'>{_esc(service.code)}</span>",
                    _esc(service.partner_id.name or ""),
                    software_name,
                    f"{current_ver} → <span style='color:{succes['texte']}; font-weight:600;'>{latest_ver}</span>",
                ])
            content_parts.append(email_tpl.get_data_table(
                [digest.env._("Service"), digest.env._("Client"), digest.env._("Software"), digest.env._("Version")],
                rows,
                header_bg=email_tpl._brand_primary(societe), company=societe,
            ))

        # Section Alertes de stockage
        if data["storage_alert_services"]:
            content_parts.append(email_tpl.get_section_title(digest.env._("Storage alerts"), avert["texte"], company=societe))
            rows = []
            for service in data["storage_alert_services"]:
                usage = service.storage_used_percent
                usage_style = f"color:{danger['texte']}; font-weight:600;" if usage >= 90 else f"color:{avert['texte']}; font-weight:600;"
                rows.append([
                    f"{_esc(service.name)}<br/><span style='color:#6B7280; font-size:12px;'>{_esc(service.code)}</span>",
                    _esc(service.partner_id.name or ""),
                    f"<span style='{usage_style}'>{usage:.1f}%</span>",
                ])
            content_parts.append(email_tpl.get_data_table(
                [digest.env._("Service"), digest.env._("Client"), digest.env._("Usage")],
                rows,
                header_bg=avert["entete"], header_color=avert["entete_texte"], company=societe,
            ))

        # Section Problèmes de santé
        if data["health_issue_services"]:
            content_parts.append(email_tpl.get_section_title(digest.env._("Health issues"), danger["texte"], company=societe))
            rows = []
            # Libellés de la sélection dans la langue du lecteur (pièges 3 et 60).
            etats_sante = dict(digest.env["hosting.service"]._fields["last_health_status"]._description_selection(digest.env))
            for service in data["health_issue_services"]:
                status = service.last_health_status or "unknown"
                libelle = etats_sante.get(service.last_health_status) or digest.env._("Unknown")
                status_badge = email_tpl.get_status_badge(status, "small", company=societe, label=libelle)
                rows.append([
                    f"{_esc(service.name)}<br/><span style='color:#6B7280; font-size:12px;'>{_esc(service.code)}</span>",
                    _esc(service.partner_id.name or ""),
                    status_badge,
                ])
            content_parts.append(email_tpl.get_data_table(
                [digest.env._("Service"), digest.env._("Client"), digest.env._("State")],
                rows,
                header_bg=danger["entete"], header_color=danger["entete_texte"], company=societe,
            ))

        # Section Maintenance en retard
        if data["maintenance_overdue"]:
            content_parts.append(email_tpl.get_section_title(digest.env._("Overdue maintenance"), danger["texte"], company=societe))
            rows = []
            for schedule in data["maintenance_overdue"]:
                # days_until_due est négatif pour une tâche échue
                days = abs(schedule.days_until_due)
                days_style = f"color:{danger['texte']}; font-weight:600;"
                maint_type = schedule.with_env(digest.env)._get_type_display()
                assigned = _esc(schedule.user_id.name) if schedule.user_id else _esc(digest.env._("Unassigned"))
                cible, code = schedule._target_display()
                retard = _esc(digest.env._("%(n)s d overdue", n=days))
                rows.append([
                    f"{_esc(cible)}<br/><span style='color:#6B7280; font-size:12px;'>{_esc(code)}</span>",
                    f"<span style='font-weight:600;'>{_esc(schedule.name)}</span><br/><span style='color:#6B7280; font-size:12px;'>{_esc(maint_type)}</span>",
                    str(schedule.next_due),
                    f"<span style='{days_style}'>{retard}</span>",
                    assigned,
                ])
            content_parts.append(email_tpl.get_data_table(
                [digest.env._("Service"), digest.env._("Maintenance task"), digest.env._("Due date"), digest.env._("Days"), digest.env._("Assigned to")],
                rows,
                header_bg=danger["entete"], header_color=danger["entete_texte"], company=societe,
            ))

        # Section Maintenance à venir
        if data["maintenance_due_soon"]:
            content_parts.append(email_tpl.get_section_title(digest.env._("Upcoming maintenance"), avert["texte"], company=societe))
            rows = []
            for schedule in data["maintenance_due_soon"]:
                days = schedule.days_until_due
                if days <= 7:
                    days_style = f"color:{avert['texte']}; font-weight:600;"
                else:
                    days_style = "color:#6B7280;"
                maint_type = schedule.with_env(digest.env)._get_type_display()
                assigned = _esc(schedule.user_id.name) if schedule.user_id else _esc(digest.env._("Unassigned"))
                cible, code = schedule._target_display()
                jours = _esc(digest.env._("%(n)s days", n=days))
                rows.append([
                    f"{_esc(cible)}<br/><span style='color:#6B7280; font-size:12px;'>{_esc(code)}</span>",
                    f"<span style='font-weight:600;'>{_esc(schedule.name)}</span><br/><span style='color:#6B7280; font-size:12px;'>{_esc(maint_type)}</span>",
                    str(schedule.next_due),
                    f"<span style='{days_style}'>{jours}</span>",
                    assigned,
                ])
            content_parts.append(email_tpl.get_data_table(
                [digest.env._("Service"), digest.env._("Maintenance task"), digest.env._("Due date"), digest.env._("Days"), digest.env._("Assigned to")],
                rows,
                header_bg=avert["entete"], header_color=avert["entete_texte"], company=societe,
            ))

        # Pied de page de contact
        content_parts.append(email_tpl.get_contact_footer(company=societe))

        content = "".join(content_parts)
        return email_tpl.get_email_wrapper(digest.env._("Services summary"), content, company=societe)

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

            # Envoyer le courriel directement, objet dans la langue du destinataire
            digest = self.with_context(lang=self._digest_lang(user))
            mail_values = {
                "subject": digest.env._("Hosting services summary - %(name)s", name=self.name),
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
                "title": self.env._("Digest sent"),
                "message": self.env._("Digest sent to %(n)s recipient(s).", n=len(self.user_ids)),
                "type": "success",
            },
        }

    def action_preview_digest(self):
        """Aperçu des données du récapitulatif."""
        self.ensure_one()
        data = self._generate_digest_data()

        message_parts = []
        if data["expiring_services"]:
            message_parts.append(self.env._(
                "Expiring: %(n)s services", n=len(data["expiring_services"])))
        if data["updates_services"]:
            message_parts.append(self.env._(
                "Updates: %(n)s services", n=len(data["updates_services"])))
        if data["storage_alert_services"]:
            message_parts.append(self.env._(
                "Storage alerts: %(n)s services", n=len(data["storage_alert_services"])))
        if data["health_issue_services"]:
            message_parts.append(self.env._(
                "Health issues: %(n)s services", n=len(data["health_issue_services"])))
        if data["maintenance_overdue"]:
            message_parts.append(self.env._(
                "Overdue maintenance: %(n)s tasks", n=len(data["maintenance_overdue"])))
        if data["maintenance_due_soon"]:
            message_parts.append(self.env._(
                "Upcoming maintenance: %(n)s tasks", n=len(data["maintenance_due_soon"])))

        if not message_parts:
            message = self.env._("Nothing to report in this digest.")
        else:
            message = "\n".join(message_parts)

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": self.env._("Digest preview"),
                "message": message,
                "type": "info",
                "sticky": True,
            },
        }
