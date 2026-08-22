"""Survey ingestion.

survey.user_input._mark_done() is the canonical completion hook (called by
the public /survey/submit controller). It can fire more than once for the
same answer (time limit + one-page + live sessions), so ingestion takes a
row lock and checks for an existing feedback before creating one - same
serialization pattern as bf_survey_upload.

Runs as the public user on website submissions: everything goes through
sudo() and never raises back into the response flow.
"""
import logging

from odoo import fields, models

_logger = logging.getLogger(__name__)


class SurveyUserInput(models.Model):
    _inherit = "survey.user_input"

    bf_cx_wave_id = fields.Many2one(
        "bf.cx.wave",
        string="Vague d'expérience",
        ondelete="set null",
        copy=False,
        index=True,
    )

    def _mark_done(self):
        res = super()._mark_done()
        for user_input in self:
            try:
                user_input._bf_cx_ingest()
            except Exception:  # noqa: BLE001 - never break survey submission
                _logger.exception(
                    "bf_cx: ingestion failed for survey.user_input %s",
                    user_input.id,
                )
        return res

    def _bf_cx_resolve_program(self):
        """Program of this answer: explicit wave first, then survey match."""
        self.ensure_one()
        program = self.bf_cx_wave_id.program_id
        if program:
            return program
        return (
            self.env["bf.cx.program"]
            .sudo()
            .search([("survey_id", "=", self.survey_id.id)], limit=1)
        )

    def _bf_cx_ingest(self):
        self.ensure_one()
        if self.test_entry:
            return
        program = self._bf_cx_resolve_program()
        if not program:
            return

        Feedback = self.env["bf.cx.feedback"].sudo()
        # Serialize concurrent _mark_done calls on the same answer, then
        # dedupe: one feedback per survey answer.
        self.env.cr.execute(
            "SELECT id FROM survey_user_input WHERE id = %s FOR NO KEY UPDATE",
            (self.id,),
        )
        if Feedback.search_count([("survey_user_input_id", "=", self.id)]):
            return

        lines = self.user_input_line_ids.filtered(lambda l: not l.skipped)

        # Score: designated scale question, else first scale answer.
        scale_lines = lines.filtered(lambda l: l.answer_type == "scale")
        if program.score_question_id:
            scale_lines = scale_lines.filtered(
                lambda l: l.question_id == program.score_question_id
            )
        score_line = scale_lines[:1]

        # Comment: designated text question, else first free-text answer.
        text_lines = lines.filtered(
            lambda l: l.answer_type in ("text_box", "char_box")
        )
        if program.comment_question_id:
            designated = text_lines.filtered(
                lambda l: l.question_id == program.comment_question_id
            )
            text_lines = designated or text_lines
        comment_line = text_lines[:1]
        comment = ""
        if comment_line:
            comment = (
                comment_line.value_text_box
                if comment_line.answer_type == "text_box"
                else comment_line.value_char_box
            ) or ""

        # Testimonial opt-in: designated simple-choice answer selected.
        testimonial_candidate = False
        if program.testimonial_answer_id:
            testimonial_candidate = any(
                l.suggested_answer_id == program.testimonial_answer_id
                for l in lines
                if l.answer_type == "suggestion"
            )

        kind = program._feedback_kind()
        if kind in ("nps", "csat") and not score_line:
            kind = "verbatim" if kind != "internal" else kind

        # Internal 360: the entry is filed under the person REVIEWED, and
        # the respondent is left out of the registry when the program asks
        # for it - lists, grouped views and exports are where a name
        # actually leaks, not the raw survey answer.
        is_internal = kind == "internal"
        hide_respondent = is_internal and program.hide_respondent
        vals = {
            "partner_id": False if hide_respondent else self.partner_id.id,
            "subject_user_id": (
                self.bf_cx_wave_id.subject_user_id.id
                if is_internal
                else False
            ),
            "date": fields.Date.context_today(self),
            "kind": kind,
            "source": "survey",
            "comment": comment,
            "program_id": program.id,
            "wave_id": self.bf_cx_wave_id.id,
            "survey_user_input_id": self.id,
            "company_id": program.company_id.id,
            "is_testimonial_candidate": testimonial_candidate,
        }
        if score_line:
            question = score_line.question_id
            vals["score"] = float(score_line.value_scale or 0)
            vals["score_max"] = float(question.scale_max or 10)
            # NPS buckets only make sense on 0-10 (enforced by constraint):
            # a program wired to a shorter scale degrades to CSAT.
            if vals["kind"] == "nps" and vals["score_max"] != 10:
                vals["kind"] = "csat"
        feedback = Feedback.create(vals)
        feedback._run_closed_loop()
        feedback._run_testimonial_candidate_loop()
        # Après la boucle fermée, pour que l'avis puisse dire qu'une activité
        # a été créée. Fermé par défaut : voir _notify_new_response.
        feedback._notify_new_response()
