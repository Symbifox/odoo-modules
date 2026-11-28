from odoo import api, fields, models
from odoo.exceptions import UserError

from ..models.survey_survey import in_order

STEP = 10


class SurveyQuestionMove(models.TransientModel):
    _name = "bf.survey.question.move"
    _description = "Move survey questions to another section"

    survey_id = fields.Many2one("survey.survey", required=True, ondelete="cascade")
    question_ids = fields.Many2many(
        "survey.question", string="To move",
        domain="[('survey_id', '=', survey_id)]",
        help="Questions or sections to move. A section moves with all of its "
             "questions; the questions keep their order.")
    target_page_id = fields.Many2one(
        "survey.question", string="Destination section",
        domain="[('survey_id', '=', survey_id), ('is_page', '=', True), "
               "('id', 'not in', question_ids)]")
    placement = fields.Selection(
        [("end", "At the end of the section"),
         ("start", "At the start of the section"),
         ("after", "After a question")],
        string="Place", required=True, default="end",
        help="For a section being moved, \"start\" places it just before the "
             "destination section and \"end\" just after the destination's "
             "last question.")
    after_question_id = fields.Many2one(
        "survey.question", string="After",
        domain="[('survey_id', '=', survey_id), ('is_page', '=', False), "
               "('page_id', '=', target_page_id), ('id', 'not in', question_ids)]")
    moves_section = fields.Boolean(compute="_compute_moves_section")
    answers_in_progress = fields.Integer(compute="_compute_answers_in_progress")

    @api.depends("question_ids.is_page")
    def _compute_moves_section(self):
        for wizard in self:
            wizard.moves_section = any(wizard.question_ids.mapped("is_page"))

    @api.depends("survey_id")
    def _compute_answers_in_progress(self):
        for wizard in self:
            wizard.answers_in_progress = self.env["survey.user_input"].search_count([
                ("survey_id", "=", wizard.survey_id.id), ("state", "=", "in_progress")])

    @api.onchange("target_page_id")
    def _onchange_target_page_id(self):
        if self.after_question_id and self.after_question_id.page_id != self.target_page_id:
            self.after_question_id = False

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        context = self.env.context
        if context.get("active_model") == "survey.question" and context.get("active_ids") \
                and not values.get("question_ids"):
            questions = self.env["survey.question"].browse(context["active_ids"])
            if len(questions.survey_id) > 1:
                raise UserError(self.env._("Questions can only be moved within one survey at a time."))
            values["question_ids"] = [(6, 0, questions.ids)]
            values.setdefault("survey_id", questions.survey_id.id)
        return values

    @api.model
    def _bf_action(self, survey, questions=None):
        context = {"default_survey_id": survey.id}
        if questions:
            context["default_question_ids"] = [(6, 0, questions.ids)]
        if self.env.context.get("bf_move_from_question"):
            context["bf_move_from_question"] = True
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Move questions"),
            "res_model": self._name,
            "view_mode": "form",
            "target": "new",
            "context": context,
        }

    # ------------------------------------------------------------------
    def _bf_new_order(self):
        """The survey's lines in the order they will have after the move."""
        self.ensure_one()
        survey = self.survey_id
        if not self.question_ids:
            raise UserError(self.env._("Choose at least one question or section to move."))
        if self.question_ids.survey_id != survey:
            raise UserError(self.env._("Every question to move must belong to the survey."))
        if not self.target_page_id:
            raise UserError(self.env._("Choose the destination section."))

        order = list(in_order(survey.question_and_page_ids))
        moving = self.question_ids
        sections = moving.filtered("is_page")
        # A section carries its questions with it.
        moving |= sections.question_ids
        strays = self.question_ids - sections - sections.question_ids
        if sections and strays:
            # Moved as one block in their old order, questions picked from
            # elsewhere would land before the section's header, in the
            # section above — or outside any section at the top.
            raise UserError(self.env._(
                "Move a section on its own, or questions on their own: a section "
                "moves with its own questions, and the others would land in the "
                "wrong section."))
        if self.target_page_id in moving:
            raise UserError(self.env._("A section cannot be moved into itself."))
        block = [q for q in order if q in moving]
        rest = [q for q in order if q not in moving]

        target = rest.index(self.target_page_id)
        next_section = next(
            (i for i in range(target + 1, len(rest)) if rest[i].is_page), len(rest))
        if sections:
            if self.placement == "after":
                raise UserError(self.env._(
                    "A section is placed before or after another section, not "
                    "after a question: it would cut that section in two."))
            index = target if self.placement == "start" else next_section
        elif self.placement == "start":
            index = target + 1
        elif self.placement == "end":
            index = next_section
        else:
            if not self.after_question_id:
                raise UserError(self.env._("Choose the question to place them after."))
            if self.after_question_id not in rest[target:next_section]:
                raise UserError(self.env._("That question is not in the destination section."))
            index = rest.index(self.after_question_id) + 1
        return order, rest[:index] + block + rest[index:]

    @staticmethod
    def _bf_misplaced(order):
        """{question: its triggers} for conditional questions placed before a trigger."""
        seen = set()
        misplaced = {}
        for question in order:
            triggers = question.triggering_answer_ids.question_id
            late = triggers.filtered(lambda t: t.id not in seen)
            if late:
                misplaced[question] = late
            seen.add(question.id)
        return misplaced

    def action_move(self):
        self.ensure_one()
        before, after = self._bf_new_order()
        already = self._bf_misplaced(before)
        broken = {q: t for q, t in self._bf_misplaced(after).items() if q not in already}
        if broken:
            lines = "\n".join(
                self.env._("• “%(question)s” would come before “%(trigger)s”, which shows it.",
                  question=question.title, trigger=", ".join(triggers.mapped("title")))
                for question, triggers in broken.items())
            raise UserError(self.env._(
                "This move would place a conditional question before the question "
                "that shows it, and the respondent would never see it:\n%s", lines))
        # Every line gets a fresh, distinct sequence. The lines of a survey
        # often share one (three questions at 172 on a real questionnaire),
        # and a tie is settled by id — so leaving it would put a moved
        # question back where it was.
        for position, question in enumerate(after, start=1):
            if question.sequence != position * STEP:
                question.sequence = position * STEP
        if self.env.context.get("bf_move_from_question"):
            # Opened from a question's own dialog, over the survey editor.
            # Closing the wizard alone would leave that dialog open, and the
            # list behind it in its old order: a line dragged in that list
            # next would write the old order back. Reloading the editor closes
            # the dialog and shows the new order.
            return {"type": "ir.actions.client", "tag": "soft_reload"}
        return {"type": "ir.actions.act_window_close"}
