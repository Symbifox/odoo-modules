import uuid
from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import UserError


class PrivacyConsent(models.Model):
    _name = "privacy.consent"
    _description = "Enregistrement de consentement"
    _inherit = ["mail.thread", "mail.activity.mixin", "privacy.framework.mixin"]
    _order = "create_date desc"
    _rec_name = "display_name"

    # Subject and Given By
    subject_partner_id = fields.Many2one(
        comodel_name="res.partner",
        string="Sujet",
        required=True,
        index=True,
        tracking=True,
        help="La personne dont le consentement est suivi",
    )
    given_by_partner_ids = fields.Many2many(
        comodel_name="res.partner",
        relation="privacy_consent_given_by_rel",
        column1="consent_id",
        column2="partner_id",
        string="Donné par",
        tracking=True,
        help="Pour les mineurs : responsables légaux autorisés à agir au nom du sujet",
    )
    is_minor = fields.Boolean(
        string="Est mineur",
        help="Cocher si le sujet a moins de 14 ans (règles spéciales Loi 25)",
    )

    # Consent Details
    notice_id = fields.Many2one(
        comodel_name="privacy.notice",
        string="Mod\u00e8le de consentement",
        index=True,
        tracking=True,
    )
    purpose_id = fields.Many2one(
        comodel_name="privacy.purpose",
        string="Objet",
        index=True,
        tracking=True,
    )
    notice_version_id = fields.Many2one(
        comodel_name="privacy.notice.version",
        string="Version du mod\u00e8le",
        tracking=True,
        domain="[('notice_id', '=', notice_id)]",
        help="La version exacte du mod\u00e8le affich\u00e9e au moment du consentement",
    )

    # Status Workflow
    status = fields.Selection(
        selection=[
            ("draft", "Brouillon"),
            ("pending", "En attente"),
            ("granted", "Accordé"),
            ("refused", "Refusé"),
            ("withdrawn", "Révoqué"),
            ("expired", "Expiré"),
        ],
        string="Statut",
        default="draft",
        required=True,
        tracking=True,
        index=True,
    )

    # Timestamps
    requested_at = fields.Datetime(
        string="Demandé le",
        help="Date d'envoi de la demande de consentement",
    )
    granted_at = fields.Datetime(
        string="Accordé le",
        tracking=True,
    )
    refused_at = fields.Datetime(
        string="Refusé le",
        tracking=True,
    )
    withdrawn_at = fields.Datetime(
        string="Révoqué le",
        tracking=True,
    )
    expires_at = fields.Datetime(
        string="Expire le",
        tracking=True,
    )

    # Collection Method
    collection_method = fields.Selection(
        selection=[
            ("portal", "Portail"),
            ("email", "Courriel"),
            ("signature", "Signature numérique"),
            ("verbal", "Verbal"),
            ("written", "Formulaire écrit"),
            ("import", "Importé"),
        ],
        string="Méthode de collecte",
        default="email",
    )

    # Context Links
    project_id = fields.Many2one(
        comodel_name="project.project",
        string="Projet",
        index=True,
        help="Projet auquel ce consentement est spécifiquement lié",
    )
    context_ref = fields.Reference(
        selection="_selection_context_ref",
        string="Référence contextuelle",
        help="Lien optionnel vers un enregistrement associé (projet, contact)",
    )

    # Computed: notice body for email templates (avoids complex Jinja in HTML)
    email_notice_body = fields.Html(
        compute="_compute_email_notice_body",
        string="Corps de l'avis (courriel)",
        sanitize=False,
    )

    # Additional Fields
    notes = fields.Text(string="Notes")
    withdrawal_reason = fields.Text(
        string="Raison de la révocation",
        help="Raison fournie lors de la révocation du consentement",
    )

    # Public Access Token (for email links without login)
    access_token = fields.Char(
        string="Jeton d'accès",
        copy=False,
        readonly=True,
        index=True,
        help="Jeton unique pour l'accès public à cette demande de consentement",
    )

    # Evidence
    evidence_ids = fields.One2many(
        comodel_name="privacy.consent.evidence",
        inverse_name="consent_id",
        string="Preuves",
    )
    evidence_count = fields.Integer(
        compute="_compute_evidence_count",
        string="Nombre de preuves",
    )

    # Company
    company_id = fields.Many2one(
        comodel_name="res.company",
        string="Société",
        default=lambda self: self.env.company,
        required=True,
    )
    active = fields.Boolean(default=True)

    # DocuSeal Integration
    docuseal_submission_id = fields.Char(
        string="ID de soumission DocuSeal",
        readonly=True,
        copy=False,
        help="ID de la soumission dans DocuSeal",
    )
    docuseal_status = fields.Selection(
        selection=[
            ("draft", "Brouillon"),
            ("pending", "Signature en attente"),
            ("completed", "Complété"),
            ("declined", "Refusé"),
            ("expired", "Expiré"),
        ],
        string="Statut DocuSeal",
        readonly=True,
        copy=False,
    )
    docuseal_sent_at = fields.Datetime(
        string="Envoyé DocuSeal le",
        readonly=True,
        copy=False,
    )
    docuseal_completed_at = fields.Datetime(
        string="Complété DocuSeal le",
        readonly=True,
        copy=False,
    )

    # LibreSign Integration
    libresign_file_uuid = fields.Char(
        string="UUID du fichier LibreSign",
        readonly=True,
        copy=False,
        help="UUID du fichier dans LibreSign",
    )
    libresign_status = fields.Selection(
        selection=[
            ("draft", "Brouillon"),
            ("pending", "Signature en attente"),
            ("completed", "Complété"),
            ("declined", "Refusé"),
            ("expired", "Expiré"),
        ],
        string="Statut LibreSign",
        readonly=True,
        tracking=True,
        copy=False,
    )
    libresign_sent_at = fields.Datetime(
        string="Envoyé LibreSign le",
        readonly=True,
        copy=False,
    )
    libresign_completed_at = fields.Datetime(
        string="Complété LibreSign le",
        readonly=True,
        copy=False,
    )

    # Email Sequence Tracking
    last_reminder_sent_at = fields.Datetime(
        string="Dernier rappel envoyé",
        readonly=True,
        copy=False,
    )
    reminder_count = fields.Integer(
        string="Nombre de rappels",
        default=0,
        readonly=True,
        copy=False,
    )

    # Renewal tracking
    renewed_from_id = fields.Many2one(
        comodel_name="privacy.consent",
        string="Renouvelé depuis",
        readonly=True,
        copy=False,
        help="Le consentement précédent à partir duquel celui-ci a été renouvelé",
    )
    renewed_to_id = fields.Many2one(
        comodel_name="privacy.consent",
        string="Renouvelé vers",
        readonly=True,
        copy=False,
        help="Le nouveau consentement créé lors du renouvellement",
    )

    # -------------------------------------------------------------------------
    # CRUD Methods
    # -------------------------------------------------------------------------

    @api.model_create_multi
    def create(self, vals_list):
        """Generate access token and auto-fill notice_id / purpose_id / guardian."""
        for vals in vals_list:
            if not vals.get('access_token'):
                vals['access_token'] = self._generate_access_token()
            # Auto-fill minor / guardian from partner
            if vals.get('subject_partner_id'):
                partner = self.env['res.partner'].browse(vals['subject_partner_id'])
                if partner.is_minor_child:
                    vals.setdefault('is_minor', True)
                    if partner.legal_guardian_ids and not vals.get('given_by_partner_ids'):
                        vals['given_by_partner_ids'] = [(6, 0, partner.legal_guardian_ids.ids)]
            # Auto-fill from notice_id
            if vals.get('notice_id'):
                notice = self.env['privacy.notice'].browse(vals['notice_id'])
                if not vals.get('purpose_id') and notice.purpose_id:
                    vals['purpose_id'] = notice.purpose_id.id
                if not vals.get('notice_version_id') and notice.current_version_id:
                    vals['notice_version_id'] = notice.current_version_id.id
            # Fallback: auto-fill from notice_version_id
            elif vals.get('notice_version_id'):
                version = self.env['privacy.notice.version'].browse(vals['notice_version_id'])
                if not vals.get('notice_id') and version.notice_id:
                    vals['notice_id'] = version.notice_id.id
                if not vals.get('purpose_id') and version.notice_id.purpose_id:
                    vals['purpose_id'] = version.notice_id.purpose_id.id
        return super().create(vals_list)

    def _generate_access_token(self):
        """Generate a unique access token for public URL access."""
        return str(uuid.uuid4())

    def get_portal_url(self):
        """Get the public portal URL for this consent (with token)."""
        self.ensure_one()
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        return f"{base_url}/privacy/consent/{self.id}/{self.access_token}"

    @api.model
    def _selection_context_ref(self):
        return [
            ("project.project", "Projet"),
            ("res.partner", "Contact"),
        ]

    @api.onchange("subject_partner_id")
    def _onchange_subject_partner_id(self):
        """Auto-fill minor info from the partner's legal guardians."""
        if self.subject_partner_id and self.subject_partner_id.is_minor_child:
            self.is_minor = True
            guardians = self.subject_partner_id.legal_guardian_ids
            if guardians:
                self.given_by_partner_ids = [(6, 0, guardians.ids)]
        elif self.subject_partner_id:
            self.is_minor = False
            self.given_by_partner_ids = [(5,)]

    @api.onchange("notice_id")
    def _onchange_notice_id(self):
        """Auto-fill purpose_id, notice_version_id and expires_at from selected notice."""
        if self.notice_id:
            self.purpose_id = self.notice_id.purpose_id
            self.notice_version_id = self.notice_id.current_version_id
            # Auto-calculate expiration from default_validity_days
            validity_days = self.notice_id.default_validity_days
            if validity_days > 0:
                self.expires_at = fields.Datetime.now() + timedelta(days=validity_days)
            else:
                self.expires_at = False
        else:
            self.purpose_id = False
            self.notice_version_id = False
            self.expires_at = False

    @api.depends("evidence_ids")
    def _compute_evidence_count(self):
        for record in self:
            record.evidence_count = len(record.evidence_ids)

    @api.depends("notice_version_id", "purpose_id")
    def _compute_email_notice_body(self):
        """Return notice body for email templates.

        Prefers the notice version body (immutable snapshot shown at consent
        time), falls back to the purpose's plain language summary.
        """
        for rec in self:
            if rec.notice_version_id and rec.notice_version_id.body:
                rec.email_notice_body = rec.notice_version_id.body
            elif rec.purpose_id and rec.purpose_id.plain_language_summary:
                rec.email_notice_body = rec.purpose_id.plain_language_summary
            else:
                rec.email_notice_body = False

    @api.depends("subject_partner_id", "notice_id", "purpose_id", "status")
    def _compute_display_name(self):
        for rec in self:
            partner = rec.subject_partner_id.name or "N/A"
            label = rec.notice_id.name or rec.purpose_id.name or "N/A"
            rec.display_name = f"{partner} - {label}"

    def _compute_expiry_date(self):
        """Calculate expiry date based on purpose validity."""
        self.ensure_one()
        validity_days = self.purpose_id.default_validity_days
        if validity_days > 0:
            return fields.Datetime.now() + timedelta(days=validity_days)
        return False

    # === Workflow Actions ===

    def action_send_request(self):
        """Send consent request (mark as pending)."""
        for consent in self:
            if consent.status != "draft":
                raise UserError("Seuls les consentements à l'état brouillon peuvent être envoyés.")
            consent.write({
                "status": "pending",
                "requested_at": fields.Datetime.now(),
            })
            consent.message_post(
                body="Demande de consentement envoyée.",
                message_type="notification",
            )
        return True

    def action_grant(self):
        """Mark consent as granted."""
        for consent in self:
            if consent.status not in ("draft", "pending"):
                raise UserError("Seuls les consentements brouillon ou en attente peuvent être accordés.")
            consent.write({
                "status": "granted",
                "granted_at": fields.Datetime.now(),
                "expires_at": consent._compute_expiry_date(),
            })
            consent.message_post(
                body="Consentement accordé.",
                message_type="notification",
            )
        return True

    def action_refuse(self):
        """Mark consent as refused."""
        for consent in self:
            if consent.status not in ("draft", "pending"):
                raise UserError("Seuls les consentements brouillon ou en attente peuvent être refusés.")
            consent.write({
                "status": "refused",
                "refused_at": fields.Datetime.now(),
            })
            consent.message_post(
                body="Consentement refusé.",
                message_type="notification",
            )
        return True

    def action_withdraw(self):
        """Open withdrawal wizard."""
        self.ensure_one()
        if self.status != "granted":
            raise UserError("Seuls les consentements accordés peuvent être révoqués.")
        return {
            "type": "ir.actions.act_window",
            "name": "Révoquer le consentement",
            "res_model": "privacy.consent.withdraw.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_consent_id": self.id},
        }

    def action_reset_to_draft(self):
        """Reset to draft status (for correction)."""
        for consent in self:
            if consent.status in ("granted", "withdrawn"):
                raise UserError("Impossible de réinitialiser les consentements accordés ou révoqués.")
            consent.write({"status": "draft"})
        return True

    def action_view_evidence(self):
        """View evidence attachments."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": f"Preuves - {self.display_name}",
            "res_model": "privacy.consent.evidence",
            "views": [[False, "list"], [False, "form"]],
            "domain": [("consent_id", "=", self.id)],
            "context": {"default_consent_id": self.id},
        }

    # === Cron Jobs ===

    @api.model
    def cron_check_expiring_consents(self):
        """Send warnings for consents expiring in 30 days."""
        warning_date = fields.Datetime.now() + timedelta(days=30)
        expiring = self.search([
            ("status", "=", "granted"),
            ("expires_at", "<=", warning_date),
            ("expires_at", ">", fields.Datetime.now()),
        ])
        for consent in expiring:
            # Check if activity already exists
            existing = self.env["mail.activity"].search([
                ("res_model", "=", "privacy.consent"),
                ("res_id", "=", consent.id),
                ("activity_type_id", "=", self.env.ref("mail.mail_activity_data_todo").id),
            ])
            if not existing:
                consent.activity_schedule(
                    "mail.mail_activity_data_todo",
                    note=f"Consentement expirant le {consent.expires_at.strftime('%Y-%m-%d')}",
                    user_id=consent.create_uid.id or self.env.user.id,
                    date_deadline=consent.expires_at.date() - timedelta(days=7),
                )

    @api.model
    def cron_mark_expired_consents(self):
        """Mark expired consents."""
        expired = self.search([
            ("status", "=", "granted"),
            ("expires_at", "<=", fields.Datetime.now()),
        ])
        for consent in expired:
            consent.write({"status": "expired"})
            consent.message_post(
                body="Le consentement a expiré.",
                message_type="notification",
            )

    # === Portal Access ===

    def _get_portal_return_action(self):
        """Return action for portal."""
        return {
            "type": "ir.actions.act_url",
            "url": "/my/privacy/consents",
        }

    def action_send_portal_link(self):
        """Re-send the consent request email (with portal link) to the contact."""
        self.ensure_one()
        if not self.subject_partner_id.email:
            raise UserError(
                "Le contact n'a pas d'adresse courriel. / "
                "The contact has no email address."
            )
        self._send_consent_request_email()
        return True

    # === Email ===

    def _ensure_portal_access(self, partner):
        """Créer un compte portail pour le contact si nécessaire.

        Le compte est créé silencieusement (sans courriel d'invitation)
        puisque le courriel de consentement sert déjà de point d'entrée.
        """
        if not partner or not partner.email:
            return
        # Vérifier s'il existe déjà un utilisateur (actif ou non)
        existing = self.env["res.users"].sudo().search([
            ("partner_id", "=", partner.id),
        ], limit=1)
        if existing:
            if not existing.active:
                existing.active = True
            return
        group_portal = self.env.ref("base.group_portal")
        self.env["res.users"].with_context(no_reset_password=True).sudo().create({
            "name": partner.name,
            "login": partner.email,
            "partner_id": partner.id,
            "groups_id": [(6, 0, [group_portal.id])],
            "active": True,
        })

    def _get_email_recipient(self):
        """Return the partner to email for this consent.

        For minors, returns the first guardian from given_by_partner_ids.
        Otherwise, returns the subject_partner_id.
        """
        self.ensure_one()
        if self.is_minor and self.given_by_partner_ids:
            return self.given_by_partner_ids[0]
        return self.subject_partner_id

    def _send_consent_request_email(self):
        """Send consent request email using template.

        The notice body HTML is injected server-side after template
        rendering via a placeholder comment, because Odoo 18's HTML
        sanitizer escapes Jinja ``{{ }}`` expressions inside ``<div>``
        elements during ``send_mail()``.

        For minors: the email is sent to all given_by_partner_ids
        (parents/guardians) and the greeting references the child.

        Security: The chatter message is sanitized to remove the consent
        link. The consent URL with access token must only be delivered
        via email to the subject — not visible in the chatter where
        anyone with record access could consent on their behalf.
        """
        self.ensure_one()
        template = self.env.ref(
            "privacy_consent.mail_template_consent_request",
            raise_if_not_found=False,
        )
        recipient = self._get_email_recipient()
        if not template or not recipient.email:
            return

        # Créer le compte portail pour le destinataire principal
        self._ensure_portal_access(recipient)

        # Pour les mineurs : créer un compte portail pour TOUS les responsables
        if self.is_minor and self.given_by_partner_ids:
            for guardian in self.given_by_partner_ids:
                if guardian.email:
                    self._ensure_portal_access(guardian)

        # Déterminer tous les responsables à notifier
        guardians_to_notify = self.env["res.partner"]
        if self.is_minor and self.given_by_partner_ids:
            guardians_to_notify = self.given_by_partner_ids.filtered("email")

        # Envoyer un courriel à chaque responsable (ou au sujet si non-mineur)
        sent_emails = []
        recipients = guardians_to_notify or recipient
        for contact in recipients:
            self._send_single_consent_email(template, contact)
            sent_emails.append(contact.email)

        # Ajouter une note au chatter
        if sent_emails:
            child = self.subject_partner_id
            if self.is_minor and guardians_to_notify:
                email_list = ", ".join(
                    f"<a href='mailto:{e}'>{e}</a>" for e in sent_emails
                )
                chatter_body = (
                    f"<p>Courriel de demande de consentement envoyé aux "
                    f"responsables de {child.name} : {email_list}.</p>"
                )
            else:
                e = sent_emails[0]
                chatter_body = (
                    f"<p>Courriel de demande de consentement envoyé à "
                    f"<a href='mailto:{e}'>{e}</a>.</p>"
                )
            self.message_post(
                body=chatter_body,
                message_type="notification",
            )

    def _send_single_consent_email(self, template, contact):
        """Envoyer un courriel de consentement à un contact spécifique.

        Gère l'injection du contenu de l'avis et la personnalisation
        pour les mineurs.
        """
        self.ensure_one()
        PLACEHOLDERS = [
            "<span>NOTICE_BODY_PLACEHOLDER</span>",
            "NOTICE_BODY_PLACEHOLDER",
        ]

        mail_id = template.send_mail(self.id, force_send=False)
        if not mail_id:
            return

        mail = self.env["mail.mail"].sudo().browse(mail_id)
        if not mail.exists():
            return

        # Rediriger vers le contact cible
        child = self.subject_partner_id
        mail.write({
            "email_to": contact.email,
            "recipient_ids": [(6, 0, [contact.id])],
        })

        # Pour les mineurs : personnaliser la salutation
        if self.is_minor and contact.id != child.id and mail.body_html:
            mail.body_html = mail.body_html.replace(
                f"Bonjour {child.name},",
                f"Bonjour {contact.name},<br/>"
                f"<span style=\"font-size:13px; color:#6B7280;\">"
                f"En tant que responsable de {child.name}</span>",
            )
            mail.body_html = mail.body_html.replace(
                f"<strong>Hello {child.name},</strong>",
                f"<strong>Hello {contact.name},</strong><br/>"
                f"<em style=\"font-size:12px; color:#9CA3AF;\">"
                f"On behalf of {child.name}</em>",
            )

        # Injecter le contenu de l'avis
        notice_body = self.email_notice_body or ""
        if mail.body_html:
            for ph in PLACEHOLDERS:
                if ph in mail.body_html:
                    mail.body_html = mail.body_html.replace(ph, notice_body)
                    break

            # Injecter la durée de validité
            days = self.purpose_id.default_validity_days
            validity_fr = (
                f"pour une durée de {days} jours"
                if days
                else "pour une durée indéterminée (sans expiration)"
            )
            validity_en = (
                f"{days} days" if days
                else "an indefinite period (no expiration)"
            )
            for tag, val in [
                ("VALIDITY_FR_PLACEHOLDER", validity_fr),
                ("VALIDITY_EN_PLACEHOLDER", validity_en),
            ]:
                mail.body_html = mail.body_html.replace(
                    f"<span>{tag}</span>", val
                ).replace(tag, val)

        mail.send()

        # Supprimer le message chatter auto-généré (on ajoute le nôtre après)
        if mail.mail_message_id:
            mail.mail_message_id.sudo().unlink()

    # === DocuSeal Integration ===

    def action_send_docuseal(self):
        """Open wizard to send for DocuSeal signature."""
        self.ensure_one()
        if self.status not in ("draft", "pending"):
            raise UserError("Seuls les consentements brouillon ou en attente peuvent être envoyés pour signature.")

        return {
            "type": "ir.actions.act_window",
            "name": "Envoyer pour signature",
            "res_model": "privacy.docuseal.send.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_consent_id": self.id},
        }

    def _process_docuseal_completion(self, submission_data, documents=None):
        """Process DocuSeal submission completion.

        Called by webhook when signature is completed.
        """
        self.ensure_one()

        # Update consent status
        self.write({
            "docuseal_status": "completed",
            "docuseal_completed_at": fields.Datetime.now(),
            "collection_method": "signature",
        })

        # Create evidence from signed documents
        if documents:
            Evidence = self.env["privacy.consent.evidence"]
            for doc in documents:
                Evidence.create({
                    "consent_id": self.id,
                    "evidence_type": "pdf_signed",
                    "attachment_file": doc.get("content"),
                    "attachment_filename": doc.get("name", "signed_consent.pdf"),
                    "note": f"Signé via DocuSeal (Soumission : {self.docuseal_submission_id})",
                })

        # Auto-grant the consent
        self.action_grant()

        self.message_post(
            body="Document signé via DocuSeal. Consentement accordé automatiquement.",
            message_type="notification",
        )

    # === LibreSign Integration ===

    def action_send_libresign(self):
        """Open wizard to send for LibreSign signature."""
        self.ensure_one()
        if self.status not in ("draft", "pending"):
            raise UserError("Seuls les consentements brouillon ou en attente peuvent être envoyés pour signature.")

        return {
            "type": "ir.actions.act_window",
            "name": "Envoyer pour signature (LibreSign)",
            "res_model": "privacy.libresign.send.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_consent_id": self.id},
        }

    def _process_libresign_completion(self, file_data, documents=None):
        """Process LibreSign file signed completion.

        Called by webhook when signature is completed.
        """
        self.ensure_one()

        # Update consent status
        self.write({
            "libresign_status": "completed",
            "libresign_completed_at": fields.Datetime.now(),
            "collection_method": "signature",
        })

        # Create evidence from signed documents
        if documents:
            Evidence = self.env["privacy.consent.evidence"]
            for doc in documents:
                Evidence.create({
                    "consent_id": self.id,
                    "evidence_type": "pdf_signed",
                    "attachment_file": doc.get("content"),
                    "attachment_filename": doc.get("name", "signed_consent.pdf"),
                    "note": f"Signé via LibreSign (UUID : {self.libresign_file_uuid})",
                })

        # Auto-grant the consent
        self.action_grant()

        self.message_post(
            body="Document signé via LibreSign. Consentement accordé automatiquement.",
            message_type="notification",
        )

    # === Renewal ===

    def action_renew(self):
        """Create a renewal consent from this one."""
        self.ensure_one()

        if self.status not in ("granted", "expired"):
            raise UserError("Seuls les consentements accordés ou expirés peuvent être renouvelés.")

        # Check if already renewed
        if self.renewed_to_id:
            raise UserError("Ce consentement a déjà été renouvelé.")

        # Create new consent
        new_consent = self.copy({
            "status": "draft",
            "requested_at": False,
            "granted_at": False,
            "refused_at": False,
            "withdrawn_at": False,
            "expires_at": False,
            "docuseal_submission_id": False,
            "docuseal_status": False,
            "docuseal_sent_at": False,
            "docuseal_completed_at": False,
            "libresign_file_uuid": False,
            "libresign_status": False,
            "libresign_sent_at": False,
            "libresign_completed_at": False,
            "last_reminder_sent_at": False,
            "reminder_count": 0,
            "renewed_from_id": self.id,
            "notes": f"Renouvelé depuis le consentement #{self.id} le {fields.Date.today()}",
        })

        # Link old consent to new one
        self.renewed_to_id = new_consent.id

        self.message_post(
            body=f"Consentement renouvelé. Nouveau consentement : #{new_consent.id}",
            message_type="notification",
        )

        return {
            "type": "ir.actions.act_window",
            "name": "Consentement renouvelé",
            "res_model": "privacy.consent",
            "view_mode": "form",
            "res_id": new_consent.id,
        }

    def action_view_renewal_history(self):
        """View the renewal chain for this consent."""
        self.ensure_one()

        # Find all related consents in the chain
        consent_ids = [self.id]

        # Go back to find original
        current = self
        while current.renewed_from_id:
            consent_ids.append(current.renewed_from_id.id)
            current = current.renewed_from_id

        # Go forward to find latest
        current = self
        while current.renewed_to_id:
            consent_ids.append(current.renewed_to_id.id)
            current = current.renewed_to_id

        return {
            "type": "ir.actions.act_window",
            "name": "Historique de renouvellement",
            "res_model": "privacy.consent",
            "views": [[False, "list"], [False, "form"]],
            "domain": [("id", "in", consent_ids)],
        }

    # === Email Sequence ===

    @api.model
    def cron_process_email_sequences(self):
        """Process email sequences for pending consents."""
        Sequence = self.env["privacy.email.sequence"]

        # Get all pending consents
        pending = self.search([
            ("status", "=", "pending"),
            ("subject_partner_id.email", "!=", False),
        ])

        for consent in pending:
            # Find applicable sequence
            sequences = Sequence.search([
                ("purpose_id", "=", consent.purpose_id.id),
                ("active", "=", True),
            ], order="sequence")

            if not sequences:
                continue

            # Determine which sequence step applies
            for seq in sequences:
                if consent.reminder_count < seq.sequence:
                    # Check if enough days have passed
                    if seq.sequence == 1:
                        # First reminder - days after initial request
                        reference_date = consent.requested_at
                    else:
                        # Subsequent reminders - days after last reminder
                        reference_date = consent.last_reminder_sent_at or consent.requested_at

                    if not reference_date:
                        continue

                    days_passed = (fields.Datetime.now() - reference_date).days
                    if days_passed >= seq.days_after_previous:
                        # Check mail tracking condition if applicable
                        if seq.only_if_not_opened:
                            # This would require mail_tracking integration
                            pass

                        # Send the reminder
                        if seq.mail_template_id:
                            seq.mail_template_id.send_mail(consent.id, force_send=True)
                            consent.write({
                                "last_reminder_sent_at": fields.Datetime.now(),
                                "reminder_count": seq.sequence,
                            })
                            consent.message_post(
                                body=f"Rappel #{seq.sequence} envoyé.",
                                message_type="notification",
                            )
                    break

    @api.model
    def cron_auto_expire_pending(self):
        """Auto-expire pending consents after configured days."""
        Purpose = self.env["privacy.purpose"]
        purposes = Purpose.search([("auto_expire_pending_days", ">", 0)])

        for purpose in purposes:
            expire_date = fields.Datetime.now() - timedelta(days=purpose.auto_expire_pending_days)
            pending = self.search([
                ("status", "=", "pending"),
                ("purpose_id", "=", purpose.id),
                ("requested_at", "<=", expire_date),
            ])

            for consent in pending:
                consent.write({"status": "expired"})
                consent.message_post(
                    body=f"La demande de consentement a expiré après {purpose.auto_expire_pending_days} jours sans réponse.",
                    message_type="notification",
                )

    # === Activity Scheduling ===

    def _schedule_follow_up_activity(self):
        """Schedule follow-up activity based on contact preferences."""
        self.ensure_one()

        # Get contact preferences
        preference = self.subject_partner_id.preference_id
        if not preference:
            # Default: schedule for tomorrow morning
            deadline = fields.Date.today() + timedelta(days=1)
        else:
            # Respect preferred contact time
            deadline = fields.Date.today() + timedelta(days=1)

        # Create activity
        activity_type = self.env.ref(
            "privacy_consent.mail_activity_type_consent_followup",
            raise_if_not_found=False,
        ) or self.env.ref("mail.mail_activity_data_call")

        self.activity_schedule(
            activity_type.id if hasattr(activity_type, "id") else "mail.mail_activity_data_call",
            note=f"Suivi de la demande de consentement pour {self.purpose_id.name}",
            user_id=self.create_uid.id or self.env.user.id,
            date_deadline=deadline,
        )
