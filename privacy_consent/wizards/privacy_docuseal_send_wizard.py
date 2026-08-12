from odoo import api, fields, models
from odoo.exceptions import UserError


class PrivacyDocusealSendWizard(models.TransientModel):
    """Assistant d'envoi pour signature DocuSeal."""

    _name = "privacy.docuseal.send.wizard"
    _description = "Envoyer pour signature DocuSeal"

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
        comodel_name="privacy.docuseal.template",
        string="Modèle DocuSeal",
        required=True,
        domain="[('purpose_id', '=', purpose_id), ('active', '=', True)]",
    )
    custom_message = fields.Text(
        string="Message personnalisé",
        help="Message personnalisé optionnel à inclure dans le courriel de demande de signature",
    )
    send_email = fields.Boolean(
        string="Envoyer une notification par courriel",
        default=True,
        help="Envoyer une notification par courriel au signataire via DocuSeal",
    )

    @api.onchange("consent_id")
    def _onchange_consent_id(self):
        """Auto-select template if only one exists for the purpose."""
        if self.consent_id and self.consent_id.purpose_id:
            templates = self.env["privacy.docuseal.template"].search([
                ("purpose_id", "=", self.consent_id.purpose_id.id),
                ("active", "=", True),
            ])
            if len(templates) == 1:
                self.template_id = templates[0]
            if templates and templates[0].custom_message:
                self.custom_message = templates[0].custom_message

    def action_send(self):
        """Send the consent for signature via DocuSeal."""
        self.ensure_one()

        if not self.partner_email:
            raise UserError("Le contact doit avoir une adresse courriel pour recevoir la demande de signature.")

        # Get DocuSeal config
        Config = self.env["privacy.docuseal.config"]
        config = Config.get_config()
        if not config:
            raise UserError("DocuSeal n'est pas configuré. Veuillez le configurer dans les Paramètres.")

        # Get field values from mapping
        field_values = self.template_id.get_field_values(self.consent_id)

        # Prepare submitter data
        submitters = [{
            "email": self.partner_email,
            "name": self.partner_id.name,
            "fields": [
                {"name": k, "default_value": v}
                for k, v in field_values.items()
            ],
        }]

        # Create submission via API
        Interface = self.env["privacy.docuseal.interface"]
        try:
            result = Interface.create_submission(
                config,
                self.template_id.docuseal_template_id,
                submitters,
                send_email=self.send_email,
                message=self.custom_message,
            )
        except Exception as e:
            raise UserError(f"Échec de la création de la soumission DocuSeal : {e}")

        # Update consent with DocuSeal info
        submission_id = result.get("id") or result.get("submission_id")
        if not submission_id:
            raise UserError("DocuSeal n'a pas retourné d'identifiant de soumission.")

        self.consent_id.write({
            "docuseal_submission_id": str(submission_id),
            "docuseal_status": "pending",
            "docuseal_sent_at": fields.Datetime.now(),
            "collection_method": "signature",
        })

        # Update consent status to pending if draft
        if self.consent_id.status == "draft":
            self.consent_id.write({
                "status": "pending",
                "requested_at": fields.Datetime.now(),
            })

        self.consent_id.message_post(
            body=f"Envoyé pour signature via DocuSeal. ID de soumission : {submission_id}",
            message_type="notification",
        )

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "Demande de signature envoyée",
                "message": f"Le document a été envoyé à {self.partner_email} pour signature.",
                "type": "success",
                "sticky": False,
            },
        }
