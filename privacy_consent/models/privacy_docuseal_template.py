from odoo import api, fields, models


class PrivacyDocusealTemplate(models.Model):
    """Correspondance entre les modèles DocuSeal et les finalités de vie privée."""

    _name = "privacy.docuseal.template"
    _description = "Correspondance de modèle DocuSeal"
    _order = "purpose_id, name"

    name = fields.Char(
        string="Nom",
        required=True,
        help="Nom interne pour cette correspondance de modèle",
    )
    purpose_id = fields.Many2one(
        comodel_name="privacy.purpose",
        string="Finalité",
        required=True,
        ondelete="cascade",
        index=True,
        help="La finalité de vie privée pour laquelle ce modèle est utilisé",
    )
    docuseal_template_id = fields.Char(
        string="ID du modèle DocuSeal",
        required=True,
        help="L'identifiant du modèle dans DocuSeal",
    )
    docuseal_template_name = fields.Char(
        string="Nom du modèle DocuSeal",
        help="Nom du modèle tel qu'affiché dans DocuSeal",
    )
    active = fields.Boolean(default=True)

    # Field Mapping
    field_mapping = fields.Text(
        string="Correspondance des champs",
        help="""Correspondance JSON des champs Odoo vers les champs du modèle DocuSeal.
Exemple :
{
    "partner_name": "subject_partner_id.name",
    "partner_email": "subject_partner_id.email",
    "purpose_name": "purpose_id.name",
    "company_name": "company_id.name"
}""",
        default="""{
    "partner_name": "subject_partner_id.name",
    "partner_email": "subject_partner_id.email",
    "purpose_name": "purpose_id.name",
    "company_name": "company_id.name"
}""",
    )

    # Email Settings
    custom_message = fields.Text(
        string="Message personnalisé",
        help="Message personnalisé à inclure dans le courriel de demande de signature",
    )

    # Company
    company_id = fields.Many2one(
        comodel_name="res.company",
        string="Société",
        default=lambda self: self.env.company,
    )

    _sql_constraints = [
        (
            "purpose_template_uniq",
            "unique(purpose_id, docuseal_template_id, company_id)",
            "Ce modèle DocuSeal est déjà associé à cette finalité !",
        ),
    ]

    @api.depends("name", "purpose_id")
    def _compute_display_name(self):
        for record in self:
            if record.purpose_id:
                record.display_name = f"{record.name} ({record.purpose_id.name})"
            else:
                record.display_name = record.name or "Nouveau modèle"

    def action_sync_template(self):
        """Synchroniser les détails du modèle depuis DocuSeal."""
        self.ensure_one()
        Config = self.env["privacy.docuseal.config"]
        Interface = self.env["privacy.docuseal.interface"]

        config = Config.get_config()
        if not config:
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": "Erreur de configuration",
                    "message": "DocuSeal n'est pas configuré pour cette société.",
                    "type": "warning",
                    "sticky": False,
                },
            }

        try:
            template = Interface.get_template(config, self.docuseal_template_id)
            self.docuseal_template_name = template.get("name", "")
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": "Modèle synchronisé",
                    "message": f"Le modèle « {self.docuseal_template_name} » a été synchronisé avec succès.",
                    "type": "success",
                    "sticky": False,
                },
            }
        except Exception as e:
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": "Échec de la synchronisation",
                    "message": str(e),
                    "type": "danger",
                    "sticky": True,
                },
            }

    def get_field_values(self, consent):
        """Get field values for a consent record based on mapping."""
        self.ensure_one()
        import json

        values = {}
        try:
            mapping = json.loads(self.field_mapping or "{}")
        except json.JSONDecodeError:
            mapping = {}

        for docuseal_field, odoo_path in mapping.items():
            value = consent
            for attr in odoo_path.split("."):
                if hasattr(value, attr):
                    value = getattr(value, attr)
                else:
                    value = ""
                    break
            values[docuseal_field] = str(value) if value else ""

        return values
