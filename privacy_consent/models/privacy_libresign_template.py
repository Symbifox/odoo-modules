import json

from odoo import api, fields, models


class PrivacyLibresignTemplate(models.Model):
    """Configuration de modèle pour les demandes de signature LibreSign."""

    _name = "privacy.libresign.template"
    _description = "Modèle LibreSign"
    _order = "purpose_id, name"

    name = fields.Char(
        string="Nom",
        required=True,
        help="Nom interne pour ce modèle",
    )
    purpose_id = fields.Many2one(
        comodel_name="privacy.purpose",
        string="Finalité",
        required=True,
        ondelete="cascade",
        index=True,
        help="La finalité de vie privée pour laquelle ce modèle est utilisé",
    )
    active = fields.Boolean(default=True)

    # PDF Template
    pdf_template = fields.Binary(
        string="Modèle PDF",
        help="Téléverser un PDF à utiliser comme modèle de document de signature",
    )
    pdf_template_filename = fields.Char(
        string="Nom du fichier PDF",
    )

    # Field Mapping
    field_mapping = fields.Text(
        string="Correspondance des champs",
        help="""Correspondance JSON des champs Odoo vers les espaces réservés du document.
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
        help="Message personnalisé à inclure dans la demande de signature",
    )

    # Company
    company_id = fields.Many2one(
        comodel_name="res.company",
        string="Société",
        default=lambda self: self.env.company,
    )

    _sql_constraints = [
        (
            "purpose_company_uniq",
            "unique(purpose_id, company_id)",
            "Un seul modèle LibreSign par finalité par société est autorisé !",
        ),
    ]

    @api.depends("name", "purpose_id")
    def _compute_display_name(self):
        for record in self:
            if record.purpose_id:
                record.display_name = f"{record.name} ({record.purpose_id.name})"
            else:
                record.display_name = record.name or "Nouveau modèle"

    def get_field_values(self, consent):
        """Get field values for a consent record based on mapping."""
        self.ensure_one()

        values = {}
        try:
            mapping = json.loads(self.field_mapping or "{}")
        except json.JSONDecodeError:
            mapping = {}

        for field_name, odoo_path in mapping.items():
            value = consent
            for attr in odoo_path.split("."):
                if hasattr(value, attr):
                    value = getattr(value, attr)
                else:
                    value = ""
                    break
            values[field_name] = str(value) if value else ""

        return values
