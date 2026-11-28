from werkzeug.exceptions import NotFound

from odoo import http
from odoo.exceptions import AccessError
from odoo.http import content_disposition, request

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class SurveyPrioritiesController(http.Controller):
    @http.route(
        "/bf_survey_priorities/trend/<int:survey_id>",
        type="http",
        auth="user",
        methods=["GET"],
    )
    def bf_survey_trend_xlsx(self, survey_id):
        # Read as the person who asked, never sudo: the responses carry their
        # own record rules. A survey they may not read answers like one that
        # does not exist, so that the route does not tell which numbers are
        # taken.
        survey = request.env["survey.survey"].browse(survey_id).exists()
        if not survey:
            raise NotFound()
        try:
            survey.check_access("read")
            # check_access alone let a survey restricted a moment ago through
            # inside a test transaction: the search applies the rule as the
            # database does. A closed survey is archived: still readable.
            if not survey.with_context(active_test=False).search(
                [("id", "=", survey.id)]
            ):
                raise NotFound()
            data = survey._bf_trend_workbook()
        except AccessError:
            raise NotFound() from None
        return request.make_response(
            data,
            headers=[
                ("Content-Type", XLSX),
                (
                    "Content-Disposition",
                    content_disposition(survey._bf_trend_filename(".xlsx")),
                ),
                ("Cache-Control", "no-store"),
            ],
        )
