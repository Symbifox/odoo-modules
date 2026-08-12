from odoo import api, fields, models


class PrivacyRight(models.Model):
    """Droit d'une personne concernée, propre à un cadre réglementaire.

    Sert au rendu des blocs « Vos droits » dans les courriels et certificats,
    en français et en anglais simultanément (colonnes `_fr`/`_en` explicites
    pour contourner la retombée silencieuse du slot jsonb `en_US`).
    """

    _name = "privacy.right"
    _description = "Droit de la personne concernée"
    _order = "sequence, id"

    framework_id = fields.Many2one(
        comodel_name="privacy.framework",
        string="Cadre juridique",
        required=True,
        ondelete="cascade",
        index=True,
    )
    code = fields.Char(string="Code", required=True)
    label_fr = fields.Char(string="Libellé (FR)")
    label_en = fields.Char(string="Libellé (EN)")
    description_fr = fields.Text(string="Description (FR)")
    description_en = fields.Text(string="Description (EN)")
    citation = fields.Char(string="Citation")
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
            "Le code de droit doit être unique par cadre et société !",
        ),
    ]

    @api.depends("label_fr", "label_en", "code")
    def _compute_display_name(self):
        for record in self:
            record.display_name = record.label_fr or record.label_en or record.code or ""
