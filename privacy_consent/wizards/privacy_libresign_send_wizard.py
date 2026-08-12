import base64

from odoo import api, fields, models
from odoo.exceptions import UserError


class PrivacyLibresignSendWizard(models.TransientModel):
    """Assistant d'envoi pour signature LibreSign."""

    _name = "privacy.libresign.send.wizard"
    _description = "Envoyer pour signature LibreSign"

    consent_id = fields.Many2one(
        comodel_name="privacy.consent",
        string="Consentement",
        required=True,
        readonly=True,
    )
    partner_id = fields.Many2one(
        related="consent_id.subject_partner_id",
        string="Sujet",
        readonly=True,
    )
    purpose_id = fields.Many2one(
        related="consent_id.purpose_id",
        string="Finalité",
        readonly=True,
    )
    partner_email = fields.Char(
        related="consent_id.subject_partner_id.email",
        string="Courriel",
        readonly=True,
    )

    template_id = fields.Many2one(
        comodel_name="privacy.libresign.template",
        string="Modèle LibreSign",
        required=True,
        domain="[('purpose_id', '=', purpose_id), ('active', '=', True)]",
    )
    custom_message = fields.Text(
        string="Message personnalisé",
        help="Message personnalisé optionnel à inclure dans la demande de signature",
    )
    send_email = fields.Boolean(
        string="Envoyer une notification par courriel",
        default=True,
        help="Envoyer une notification par courriel au signataire via LibreSign",
    )

    @api.onchange("consent_id")
    def _onchange_consent_id(self):
        """Auto-select template if only one exists for the purpose."""
        if self.consent_id and self.consent_id.purpose_id:
            templates = self.env["privacy.libresign.template"].search([
                ("purpose_id", "=", self.consent_id.purpose_id.id),
                ("active", "=", True),
            ])
            if len(templates) == 1:
                self.template_id = templates[0]
            if templates and templates[0].custom_message:
                self.custom_message = templates[0].custom_message

    def action_send(self):
        """Send the consent for signature via LibreSign."""
        self.ensure_one()

        if not self.partner_email:
            raise UserError("Le contact doit avoir une adresse courriel pour recevoir la demande de signature.")

        # Get LibreSign config
        Config = self.env["privacy.libresign.config"]
        config = Config.get_config()
        if not config:
            raise UserError("LibreSign n'est pas configuré. Veuillez le configurer dans les Paramètres.")

        # Get PDF content — use template PDF or generate from consent
        if self.template_id.pdf_template:
            file_data = self.template_id.pdf_template
        else:
            # Generate a consent PDF via report if available
            report = self.env.ref(
                "privacy_consent.action_report_consent_document",
                raise_if_not_found=False,
            )
            if report:
                pdf_content, _content_type = report._render_qweb_pdf(
                    report.id, [self.consent_id.id]
                )
                file_data = base64.b64encode(pdf_content)
            else:
                raise UserError(
                    "Aucun modèle PDF configuré sur le modèle LibreSign "
                    "et aucun rapport de consentement n'est disponible."
                )

        # Prepare signer data
        signers = [{
            "email": self.partner_email,
            "name": self.partner_id.name,
        }]

        doc_name = f"Consentement - {self.purpose_id.name} - {self.partner_id.name}"

        # Send signature request via API
        Interface = self.env["privacy.libresign.interface"]
        try:
            result = Interface.request_signature(
                config,
                file_data,
                signers,
                name=doc_name,
            )
        except Exception as e:
            raise UserError(f"Échec de la création de la demande de signature LibreSign : {e}")

        # Extract file UUID from response
        file_uuid = (
            result.get("uuid")
            or result.get("file", {}).get("uuid")
            or result.get("data", {}).get("uuid")
        )
        if not file_uuid:
            raise UserError("LibreSign n'a pas retourné d'UUID de fichier.")

        self.consent_id.write({
            "libresign_file_uuid": str(file_uuid),
            "libresign_status": "pending",
            "libresign_sent_at": fields.Datetime.now(),
            "collection_method": "signature",
        })

        # Update consent status to pending if draft
        if self.consent_id.status == "draft":
            self.consent_id.write({
                "status": "pending",
                "requested_at": fields.Datetime.now(),
            })

        self.consent_id.message_post(
            body=f"Envoyé pour signature via LibreSign. UUID du fichier : {file_uuid}",
            message_type="notification",
        )

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "Demande de signature envoyée",
                "message": f"Le document a été envoyé à {self.partner_email} pour signature via LibreSign.",
                "type": "success",
                "sticky": False,
            },
        }
