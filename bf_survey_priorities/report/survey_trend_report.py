from odoo import api, models
from odoo.exceptions import AccessError


class ReportSurveyTrend(models.AbstractModel):
    _name = "report.bf_survey_priorities.report_bf_survey_trend"
    _description = "Survey weighted trend PDF"

    @api.model
    def _get_report_values(self, docids, data=None):
        # Read as the person who prints: whoever cannot open the survey gets
        # Odoo's access error before anything is drawn.
        docs = self.env["survey.survey"].browse(docids)
        docs.check_access("read")
        # The rule as the database applies it; a closed survey is archived.
        readable = docs.with_context(active_test=False).search([("id", "in", docs.ids)])
        if set(readable.ids) != set(docs.ids):
            raise AccessError(self.env._("You cannot read this survey."))
        return {"doc_ids": docids, "doc_model": "survey.survey", "docs": docs}
