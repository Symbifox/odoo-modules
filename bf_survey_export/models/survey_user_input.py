from odoo import models
from odoo.tools import format_date, format_datetime, float_repr

from .survey_survey import _UNSAFE_FILENAME

CHOICE = {"simple_choice", "multiple_choice"}


def _number(value):
    """12.0 → "12", 12.5 → "12.5": what the respondent typed, not a float."""
    if value == int(value):
        return str(int(value))
    return float_repr(value, 2).rstrip("0").rstrip(".")


class SurveyUserInput(models.Model):
    _inherit = "survey.user_input"

    def action_bf_export_answers_pdf(self):
        """Print this response with the survey's PDF theme."""
        self.ensure_one()
        return self.env.ref(
            "bf_survey_export.action_report_bf_survey_answers"
        ).report_action(self)

    def _bf_respondent(self):
        self.ensure_one()
        return self.partner_id.name or self.email or self.nickname or ""

    def _bf_pdf_filename(self):
        """The PDF's name, without its extension (Odoo adds it)."""
        self.ensure_one()
        parts = [self.survey_id.title or "", self._bf_respondent()]
        name = " - ".join(
            " ".join(_UNSAFE_FILENAME.sub(" ", p).split()) for p in parts if p
        )
        name = name.strip(" .-")[:180].strip(" .-")
        return name or self.env._("Response")

    def _bf_pdf_meta(self):
        """The three facts printed under the header band."""
        self.ensure_one()
        _ = self.env._
        if self.state == "done":
            when = format_datetime(
                self.env, self.end_datetime or self.write_date, dt_format="short"
            )
            when_label = _("Submitted on")
        else:
            when = dict(self._fields["state"]._description_selection(self.env)).get(
                self.state, self.state
            )
            when_label = _("Status")
        company = self.partner_id.commercial_company_name or ""
        if company == self.partner_id.name:
            company = ""
        return [
            (_("Respondent"), self._bf_respondent() or "—"),
            (_("Organisation"), company or "—"),
            (when_label, when),
        ]

    def _bf_answer_sections(self):
        """The response as it is printed: its sections, and for each question
        the respondent was shown, what they answered.

        A question hidden by its condition is left out, as Odoo leaves it out
        of its own print (_get_print_questions). A choice question shows the
        choices ticked, never the others. Nothing of the answer key is read:
        which choice was right and the points it gave stay out of the PDF.
        """
        self.ensure_one()
        survey = self.survey_id
        shown = self._get_print_questions()
        if self.predefined_question_ids:
            # Questions drawn at random: only those this respondent was given,
            # as Odoo's own print does.
            shown &= self.predefined_question_ids
        lines = self.user_input_line_ids.filtered(lambda line: not line.skipped)
        by_question = {}
        for line in lines:
            by_question.setdefault(line.question_id, self.env["survey.user_input.line"])
            by_question[line.question_id] |= line

        sections = []
        current = None
        for item in survey.question_and_page_ids:
            if item.is_page:
                current = {
                    "title": survey._bf_pdf_numbered_title(item),
                    "items": [],
                }
                sections.append(current)
                continue
            if item not in shown:
                continue
            if current is None:
                current = {"title": "", "items": []}
                sections.append(current)
            answer = self._bf_answer(item, by_question.get(item))
            current["items"].append(
                {
                    "title": survey._bf_pdf_numbered_title(item),
                    **answer,
                }
            )
        # A section whose questions were all hidden from this respondent is
        # not theirs to fill: it is left out rather than printed as empty.
        sections = [s for s in sections if s["items"]]
        for section in sections:
            section["answered"] = any(i["kind"] != "none" for i in section["items"])
        return sections

    def _bf_answer(self, question, lines):
        """One question's answer: ``kind`` says how it is drawn."""
        if not lines:
            return {"kind": "none"}
        qtype = question.question_type
        if qtype in CHOICE:
            return self._bf_answer_choices(question, lines)
        if qtype == "matrix":
            return self._bf_answer_matrix(question, lines)
        if qtype == "percent_allocation" or (
            question.matrix_row_ids and any(lines.mapped("matrix_row_id"))
        ):
            return self._bf_answer_bars(question, lines)
        return self._bf_answer_value(lines[:1])

    def _bf_answer_comments(self, question, lines):
        """The "please specify" texts, each under the name of its own box."""
        _ = self.env._
        label = (question.comments_message or _("If other, please specify:")).strip()
        return [
            (label, line.value_char_box or line.value_text_box)
            for line in lines
            if not line.suggested_answer_id
            and not line.matrix_row_id
            and (line.value_char_box or line.value_text_box)
        ]

    def _bf_answer_choices(self, question, lines):
        """The choices ticked, never the others, and the "please specify"
        text under the name of its own box."""
        _ = self.env._
        ticked = lines.filtered("suggested_answer_id")
        return {
            "kind": "choices",
            "values": [
                line.suggested_answer_id.value or _("(image)") for line in ticked
            ],
            "comments": self._bf_answer_comments(question, lines),
        }

    def _bf_answer_matrix(self, question, lines):
        rows = []
        for row in question.matrix_row_ids:
            chosen = lines.filtered(lambda line, r=row: line.matrix_row_id == r)
            rows.append(
                (
                    row.value or "",
                    ", ".join(chosen.mapped("suggested_answer_id.value")) or "—",
                )
            )
        return {
            "kind": "matrix",
            "rows": rows,
            "comments": self._bf_answer_comments(question, lines),
        }

    def _bf_answer_bars(self, question, lines):
        """A percentage allocation: one number per row, drawn as a bar."""
        rows = []
        for row in question.matrix_row_ids:
            value = sum(
                lines.filtered(lambda line, r=row: line.matrix_row_id == r).mapped(
                    "value_numerical_box"
                )
            )
            if value:
                rows.append(
                    (row.value or "", _number(value), max(1, min(100, round(value))))
                )
        return {"kind": "bars", "rows": rows} if rows else {"kind": "none"}

    def _bf_answer_value(self, line):
        """A typed answer: text, number, date or scale."""
        if line.answer_type == "text_box":
            return {"kind": "long", "value": line.value_text_box or ""}
        if line.answer_type == "numerical_box":
            return {"kind": "text", "value": _number(line.value_numerical_box)}
        if line.answer_type == "date":
            return {"kind": "text", "value": format_date(self.env, line.value_date)}
        if line.answer_type == "datetime":
            return {
                "kind": "text",
                "value": format_datetime(
                    self.env, line.value_datetime, dt_format="short"
                ),
            }
        if line.answer_type == "scale":
            return {"kind": "text", "value": str(line.value_scale)}
        value = line.value_char_box or line.value_text_box or ""
        return {"kind": "text", "value": value} if value else {"kind": "none"}
