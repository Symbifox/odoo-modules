from odoo import fields, models


class SurveySurvey(models.Model):
    _inherit = "survey.survey"

    certification_report_layout = fields.Selection(
        selection_add=[("bf_brand", "Company brand")],
        ondelete={"bf_brand": "set default"},
    )
