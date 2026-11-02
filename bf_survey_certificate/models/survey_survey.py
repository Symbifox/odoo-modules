from odoo import fields, models


class SurveySurvey(models.Model):
    _inherit = "survey.survey"

    # Three branded layouts. "bf_brand" (the panel) keeps the key the first
    # version shipped under, so every survey already set to the company brand
    # moves to the panel design without a migration.
    certification_report_layout = fields.Selection(
        selection_add=[
            ("bf_brand", "Company brand — panel"),
            ("bf_brand_sleek", "Company brand — minimal"),
            ("bf_brand_classic", "Company brand — classic"),
        ],
        ondelete={
            "bf_brand": "set default",
            "bf_brand_sleek": "set default",
            "bf_brand_classic": "set default",
        },
    )
