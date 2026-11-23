from werkzeug.exceptions import NotFound

from odoo import http
from odoo.exceptions import AccessError
from odoo.http import content_disposition, request

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class SurveyExportController(http.Controller):
    @http.route(
        "/bf_survey_export/questions/<int:survey_id>",
        type="http",
        auth="user",
        methods=["GET"],
    )
    def bf_survey_questions_xlsx(self, survey_id):
        # Read as the person who asked, never sudo: someone who cannot open
        # the survey in Odoo gets nothing out of it here either. A survey they
        # may not read answers like one that does not exist, so that the
        # route does not tell which numbers are taken.
        survey = request.env["survey.survey"].browse(survey_id).exists()
        if not survey:
            raise NotFound()
        try:
            survey.check_access("read")
            # The questions and their answers carry their own record rules.
            data = survey._bf_questions_workbook()
        except AccessError:
            raise NotFound() from None
        return request.make_response(
            data,
            headers=[
                ("Content-Type", XLSX),
                (
                    "Content-Disposition",
                    content_disposition(survey._bf_questions_filename()),
                ),
                ("Cache-Control", "no-store"),
            ],
        )
