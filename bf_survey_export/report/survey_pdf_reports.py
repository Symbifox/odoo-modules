from odoo import api, models


class ReportSurveyQuestions(models.AbstractModel):
    _name = "report.bf_survey_export.report_bf_survey_questions"
    _description = "Survey questionnaire PDF"

    @api.model
    def _get_report_values(self, docids, data=None):
        # Read as the person who prints: whoever cannot open the survey gets
        # Odoo's access error before anything is drawn.
        docs = self.env["survey.survey"].browse(docids)
        docs.check_access("read")
        return {"doc_ids": docids, "doc_model": "survey.survey", "docs": docs}


class ReportSurveyAnswers(models.AbstractModel):
    _name = "report.bf_survey_export.report_bf_survey_answers"
    _description = "Survey response PDF"

    @api.model
    def _get_report_values(self, docids, data=None):
        docs = self.env["survey.user_input"].browse(docids)
        # The rule on responses already carries the survey's restriction to
        # some users: no separate check on the survey is needed.
        docs.check_access("read")
        return {"doc_ids": docids, "doc_model": "survey.user_input", "docs": docs}
