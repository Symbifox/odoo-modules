import re

from odoo import api, fields, models

# A number typed at the start of a title by hand: "1. ", "2.1 ", "2.12.1 ",
# "3.4. ". At least one dot is required, so that a title that merely starts
# with a figure ("2026 objectives", "15 minutes of your time") is left alone.
TYPED_NUMBER = re.compile(r"^\s*(?:\d+\.)+\d*\s+")


class SurveyQuestion(models.Model):
    _inherit = "survey.question"

    # Not stored, on purpose. A stored number would be rewritten on every
    # question of a survey each time one of them moves — stamping write_date on
    # questions nobody touched — and installing the module would stamp every
    # question of every survey. Computed on read, it costs one pass over a
    # survey's lines and is always in step with their order.
    bf_number = fields.Char(
        string="Number",
        compute="_compute_bf_number",
        help="The section's or question's number, when the survey numbers "
             "its questions: 2 for a section, 2.1 for its first question, "
             "2.1.1 for a conditional question shown by 2.1.",
    )

    @api.depends(
        "survey_id.bf_numbering",
        "survey_id.question_and_page_ids.sequence",
        "survey_id.question_and_page_ids.is_page",
        "survey_id.question_and_page_ids.triggering_answer_ids",
    )
    def _compute_bf_number(self):
        numbers = {}
        for survey in self.survey_id:
            numbers.update(survey._bf_numbers())
        for question in self:
            question.bf_number = numbers.get(question._origin.id or question.id, False)

    @api.depends("title", "bf_number", "is_page")
    def _compute_display_name(self):
        super()._compute_display_name()
        for question in self:
            if question.bf_number:
                # "2. Your company" for a section, as the pages show it.
                number = f"{question.bf_number}." if question.is_page else question.bf_number
                question.display_name = f"{number} {question.display_name or ''}".strip()

    def _bf_typed_number(self, lang=None):
        """The number typed at the start of this title, or "" if none."""
        self.ensure_one()
        title = (self.with_context(lang=lang) if lang else self).title or ""
        match = TYPED_NUMBER.match(title)
        return match.group(0) if match else ""

    def action_bf_move(self):
        """Open the move form on this question (or section)."""
        self.ensure_one()
        return self.env["bf.survey.question.move"]._bf_action(self.survey_id, self)
