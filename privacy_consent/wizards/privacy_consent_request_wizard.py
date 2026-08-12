from datetime import timedelta
from markupsafe import Markup

from odoo import api, fields, models
from odoo.exceptions import UserError


class PrivacyConsentRequestWizard(models.TransientModel):
    _name = "privacy.consent.request.wizard"
    _description = "Assistant de demande de consentement"

    partner_ids = fields.Many2many(
        comodel_name="res.partner",
        string="Sujets du consentement",
        required=True,
        help="Contacts pour lesquels un consentement est demandé. "
        "Pour les personnes mineures, les courriels seront envoyés aux responsables légaux.",
    )
    email_recipients_info = fields.Html(
        string="Destinataires des courriels",
        compute="_compute_email_recipients_info",
        sanitize=False,
    )
    group_id = fields.Many2one(
        comodel_name="privacy.consent.group",
        string="Groupe de consentement",
        help="Sélectionner un groupe pour demander plusieurs consentements simultanément",
    )
    notice_id = fields.Many2one(
        comodel_name="privacy.notice",
        string="Modèle de consentement",
        domain="[('requires_consent', '=', True)]",
    )
    purpose_id = fields.Many2one(
        comodel_name="privacy.purpose",
        string="Objet",
    )
    project_id = fields.Many2one(
        comodel_name="project.project",
        string="Projet",
        help="Lier optionnellement le consentement à un projet spécifique",
    )
    send_email = fields.Boolean(
        string="Envoyer une notification par courriel",
        default=True,
        help="Envoyer un courriel aux destinataires pour demander leur consentement",
    )
    collection_method = fields.Selection(
        selection=[
            ("portal", "Portail"),
            ("email", "Courriel"),
            ("verbal", "Verbal"),
            ("written", "Formulaire écrit"),
        ],
        string="Méthode de collecte",
        default="email",
        required=True,
    )

    @api.depends("partner_ids")
    def _compute_email_recipients_info(self):
        """Compute who will actually receive the emails."""
        for wizard in self:
            if not wizard.partner_ids:
                wizard.email_recipients_info = False
                continue

            lines = []
            for partner in wizard.partner_ids:
                if partner.is_minor_child and partner.legal_guardian_ids:
                    guardians = partner.legal_guardian_ids.filtered("email")
                    if guardians:
                        names = ", ".join(
                            f"<strong>{g.name}</strong> ({g.email})"
                            for g in guardians
                        )
                        lines.append(
                            f"<li>{partner.name} (mineur) → {names}</li>"
                        )
                    else:
                        lines.append(
                            f'<li>{partner.name} (mineur) → '
                            f'<span style="color: #dc3545;">Aucun responsable avec courriel</span></li>'
                        )
                elif partner.email:
                    lines.append(
                        f"<li><strong>{partner.name}</strong> ({partner.email})</li>"
                    )
                else:
                    lines.append(
                        f'<li>{partner.name} → '
                        f'<span style="color: #dc3545;">Aucun courriel</span></li>'
                    )

            wizard.email_recipients_info = Markup(
                f"<ul style='margin:0; padding-left:16px;'>{''.join(lines)}</ul>"
            )

    @api.onchange("group_id")
    def _onchange_group_id(self):
        if self.group_id:
            self.notice_id = False
            self.purpose_id = False

    @api.onchange("notice_id")
    def _onchange_notice_id(self):
        if self.notice_id:
            self.purpose_id = self.notice_id.purpose_id
            self.group_id = False
        else:
            self.purpose_id = False

    def _get_notices(self):
        """Return the notices to process (from group or single notice)."""
        if self.group_id and self.group_id.notice_ids:
            return self.group_id.notice_ids
        elif self.notice_id:
            return self.notice_id
        return self.env["privacy.notice"]

    def _get_or_create_notice_version(self, notice):
        """Get current version or create one if needed."""
        version = notice.current_version_id
        if not version:
            lang = self.env.user.lang or "fr_CA"
            body = notice.body_fr if lang.startswith("fr") else (notice.body_en or notice.body_fr)
            if not body:
                raise UserError(
                    f"Le modèle « {notice.name} » n'a pas de contenu. "
                    "Veuillez ajouter du contenu avant de demander le consentement."
                )
            version = self.env["privacy.notice.version"].create({
                "notice_id": notice.id,
                "version": "1.0",
                "body": body,
                "effective_date": fields.Date.today(),
            })
        return version

    def action_create_requests(self):
        """Create consent records for all selected partners and notices."""
        self.ensure_one()

        if not self.partner_ids:
            raise UserError("Veuillez sélectionner au moins un destinataire.")

        notices = self._get_notices()
        if not notices:
            raise UserError(
                "Veuillez sélectionner un modèle de consentement ou un groupe."
            )

        Consent = self.env["privacy.consent"]
        created = Consent

        for notice in notices:
            purpose = notice.purpose_id
            notice_version = self._get_or_create_notice_version(notice)

            # Compute expiration from validity days
            validity_days = notice.default_validity_days
            expires_at = (
                fields.Datetime.now() + timedelta(days=validity_days)
                if validity_days > 0
                else False
            )

            for partner in self.partner_ids:
                # Check if consent already exists for this partner/purpose
                existing = Consent.search([
                    ("subject_partner_id", "=", partner.id),
                    ("purpose_id", "=", purpose.id),
                    ("status", "in", ("draft", "pending", "granted")),
                ], limit=1)

                if existing:
                    continue

                consent = Consent.create({
                    "subject_partner_id": partner.id,
                    "notice_id": notice.id,
                    "purpose_id": purpose.id,
                    "notice_version_id": notice_version.id if notice_version else False,
                    "status": "pending" if self.send_email else "draft",
                    "collection_method": self.collection_method,
                    "project_id": self.project_id.id if self.project_id else False,
                    "requested_at": fields.Datetime.now() if self.send_email else False,
                    "expires_at": expires_at,
                })
                created |= consent

                if self.send_email:
                    consent._send_consent_request_email()

        if not created:
            raise UserError(
                "Tous les contacts sélectionnés possèdent déjà un consentement "
                "en attente ou actif pour les modèles sélectionnés."
            )

        if len(created) == 1:
            return {
                "type": "ir.actions.act_window",
                "name": "Demande de consentement",
                "res_model": "privacy.consent",
                "view_mode": "form",
                "res_id": created.id,
            }
        else:
            return {
                "type": "ir.actions.act_window",
                "name": "Demandes de consentement créées",
                "res_model": "privacy.consent",
                "views": [[False, "list"], [False, "form"]],
                "domain": [("id", "in", created.ids)],
            }
