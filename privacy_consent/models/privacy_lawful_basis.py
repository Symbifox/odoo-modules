from odoo import api, fields, models


class PrivacyLawfulBasis(models.Model):
    """Base légale de traitement, propre à un cadre réglementaire.

    Ex. : les 6 bases du GDPR (consentement, contrat, obligation légale,
    intérêts vitaux, mission d'intérêt public, intérêt légitime) ou les bases
    de la Loi 25 (consentement, nécessité contractuelle, autorisation légale,
    intérêt légitime).
    """

    _name = "privacy.lawful.basis"
    _description = "Base légale de traitement"
    _order = "sequence, id"

    framework_id = fields.Many2one(
        comodel_name="privacy.framework",
        string="Cadre juridique",
        required=True,
        ondelete="cascade",
        index=True,
    )
    code = fields.Char(string="Code", required=True)
    name = fields.Char(string="Nom", required=True, translate=True)
    description = fields.Text(string="Description", translate=True)
    citation = fields.Char(string="Citation")
    requires_consent = fields.Boolean(
        string="Repose sur le consentement",
        help="Cette base requiert le consentement explicite de la personne",
    )
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        comodel_name="res.company",
        string="Société",
        default=False,
    )

    _sql_constraints = [
        (
            "code_framework_company_uniq",
            "unique(code, framework_id, company_id)",
            "Le code de base légale doit être unique par cadre et société !",
        ),
    ]

    @api.depends("name", "framework_id")
    def _compute_display_name(self):
        for record in self:
            if record.framework_id:
                record.display_name = f"[{record.framework_id.law_short_label or record.framework_id.code}] {record.name}"
            else:
                record.display_name = record.name or ""
