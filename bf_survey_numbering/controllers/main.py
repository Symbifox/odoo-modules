from odoo.addons.survey.controllers.main import Survey


class SurveyNumbering(Survey):

    def _prepare_survey_data(self, survey_sudo, answer_sudo, **post):
        """The breadcrumb of the fill form carries the sections' numbers too.

        The core builds it from the bare page titles, in Python, and the
        breadcrumb widget prints them as given: the template inheritance that
        numbers the page heading never reaches it.
        """
        data = super()._prepare_survey_data(survey_sudo, answer_sudo, **post)
        if survey_sudo.bf_numbering and data.get("breadcrumb_pages"):
            numbers = {page.id: page.bf_number for page in survey_sudo.page_ids}
            for page in data["breadcrumb_pages"]:
                if numbers.get(page["id"]):
                    page["title"] = f"{numbers[page['id']]}. {page['title']}"
        return data
