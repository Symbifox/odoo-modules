import io

import xlsxwriter

from odoo import api, fields, models
from odoo.tools.misc import get_lang

from odoo.addons.bf_survey_export.models.survey_survey import (
    _CELL_MAX,
    _UNSAFE_FILENAME,
)


class SurveySurvey(models.Model):
    _inherit = "survey.survey"

    bf_rank_has_weighted = fields.Boolean(
        string="Has a weighted matrix",
        compute="_compute_bf_rank_has_weighted",
    )

    @api.depends(
        "question_and_page_ids.question_type",
        "question_and_page_ids.suggested_answer_ids.bf_rank_points",
    )
    def _compute_bf_rank_has_weighted(self):
        for survey in self:
            survey.bf_rank_has_weighted = any(
                survey.question_and_page_ids.mapped("bf_rank_weighted")
            )

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def action_bf_export_trend_xlsx(self):
        """Download the weighted trend workbook of this survey."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_url",
            "url": f"/bf_survey_priorities/trend/{self.id}",
            "target": "download",
        }

    def action_bf_export_trend_pdf(self):
        """Print the weighted trend of this survey."""
        self.ensure_one()
        return self.env.ref(
            "bf_survey_priorities.action_report_bf_survey_trend"
        ).report_action(self)

    def _bf_trend_filename(self, extension=""):
        self.ensure_one()
        title = " ".join(_UNSAFE_FILENAME.sub(" ", self.title or "").split())
        title = title.strip(" .-")[:150].strip(" .-")
        name = title or self.env._("Survey")
        return f"{name} - {self.env._('weighted trend')}{extension}"

    def _bf_num(self, value):
        """A number in the reader's language, without trailing zeros:
        10 -> "10", 2.5 -> "2,5" in French."""
        lang = self.env["res.lang"].browse(get_lang(self.env).id)
        text = lang.format("%.2f", value or 0.0, grouping=True)
        if lang.decimal_point and lang.decimal_point in text:
            text = text.rstrip("0").rstrip(lang.decimal_point)
        return text

    # ------------------------------------------------------------------
    # Reading the trend
    # ------------------------------------------------------------------
    def _bf_trend_questions(self):
        self.ensure_one()
        return self.question_ids.filtered("bf_rank_weighted").sorted(
            lambda q: (q.sequence, q.id)
        )

    def _bf_trend(self):
        """The weighted trend of each weighted matrix of this survey, over the
        completed responses that are not test entries. Read as the current
        user: the record rules on responses apply."""
        self.ensure_one()
        questions = self._bf_trend_questions()
        lines = self.env["survey.user_input.line"]
        if questions:
            lines = lines.search(
                [
                    ("question_id", "in", questions.ids),
                    ("skipped", "=", False),
                    ("user_input_id.test_entry", "=", False),
                    ("user_input_id.state", "=", "done"),
                ]
            )
        completed = len(
            self.env["survey.user_input"].search(
                [
                    ("survey_id", "=", self.id),
                    ("test_entry", "=", False),
                    ("state", "=", "done"),
                ]
            )
        )
        return {
            "completed": completed,
            "questions": [
                {
                    "question": question,
                    "title": self._bf_pdf_numbered_title(question),
                    "rule": question._bf_rank_rule(),
                    **question._bf_rank_trend(lines),
                }
                for question in questions
            ],
        }

    def _bf_trend_summary(self, item):
        """ "4 respondents, 1 of whom ticked several rows in one column." """
        _ = self.env._
        text = _("%(count)s respondent(s)", count=item["respondents"])
        if item["shared"]:
            text += " · " + _(
                "%(count)s ticked several rows in one column: "
                "those columns' points are shared",
                count=item["shared"],
            )
        return text

    def _bf_trend_workbook(self):
        """The weighted trend of this survey, as bytes (.xlsx)."""
        self.ensure_one()
        _ = self.env._
        trend = self._bf_trend()
        truncated = _("[truncated]")

        def text(value):
            value = value or ""
            if len(value) > _CELL_MAX:
                value = value[: _CELL_MAX - len(truncated) - 2] + "… " + truncated
            return value

        buffer = io.BytesIO()
        # Labels are written with write_string: a row that starts with "=" or
        # "http" is text a person typed, never a formula nor a link.
        book = xlsxwriter.Workbook(
            buffer,
            {
                "in_memory": True,
                "strings_to_formulas": False,
                "strings_to_urls": False,
                "strings_to_numbers": False,
            },
        )
        border = {"border": 1, "border_color": "#BFBFBF"}
        title_fmt = book.add_format({"bold": True, "font_size": 14})
        note_fmt = book.add_format({"italic": True, "font_color": "#555555"})
        question_fmt = book.add_format({"bold": True, "font_size": 12})
        head_fmt = book.add_format(
            {
                "bold": True,
                "bg_color": "#DDEBF7",
                "text_wrap": True,
                "valign": "top",
                **border,
            }
        )
        cell_fmt = book.add_format({"text_wrap": True, "valign": "top", **border})
        num_fmt = book.add_format({"num_format": "0.00", "valign": "top", **border})
        pct_fmt = book.add_format({"num_format": "0.0", "valign": "top", **border})
        int_fmt = book.add_format({"num_format": "0", "valign": "top", **border})

        sheet = book.add_worksheet(_("Weighted trend")[:31])
        sheet.write_string(0, 0, text(self.title), title_fmt)
        sheet.write_string(
            1,
            0,
            _(
                "Completed responses, test entries left out: %(count)s. "
                "Several rows ticked in one column share that column's points.",
                count=trend["completed"],
            ),
            note_fmt,
        )
        sheet.set_column(0, 0, 7)
        sheet.set_column(1, 1, 60)
        sheet.set_column(2, 2, 18)
        sheet.set_column(3, 4, 11)
        width = max([len(item["columns"]) for item in trend["questions"]] or [0])
        if width:
            sheet.set_column(5, 4 + width, 13)

        r = 3
        if not trend["questions"]:
            sheet.write_string(
                r, 0, _("No matrix of this survey has points on its columns."), note_fmt
            )
        for item in trend["questions"]:
            sheet.write_string(r, 0, text(item["title"]), question_fmt)
            r += 1
            sheet.write_string(r, 0, text(item["rule"]), note_fmt)
            r += 1
            sheet.write_string(r, 0, self._bf_trend_summary(item), note_fmt)
            r += 1
            headers = [_("Rank"), _("Row"), _("Group"), _("Points"), _("Share (%)")]
            headers += [_("Mentions: %s", c["label"]) for c in item["columns"]]
            for col, label in enumerate(headers):
                sheet.write_string(r, col, text(label), head_fmt)
            r += 1
            for row in item["rows"]:
                if row["rank"]:
                    sheet.write_number(r, 0, row["rank"], int_fmt)
                else:
                    sheet.write_blank(r, 0, None, int_fmt)
                sheet.write_string(r, 1, text(row["label"]), cell_fmt)
                sheet.write_string(r, 2, text(row["group"]), cell_fmt)
                sheet.write_number(r, 3, row["points"], num_fmt)
                sheet.write_number(r, 4, row["share"], pct_fmt)
                for offset, count in enumerate(row["mentions"]):
                    sheet.write_number(r, 5 + offset, count, int_fmt)
                r += 1
            if item["groups"]:
                r += 1
                for col, label in enumerate(
                    [_("Rank"), _("Group"), _("Rows"), _("Points"), _("Share (%)")]
                ):
                    sheet.write_string(r, col, label, head_fmt)
                r += 1
                for group in item["groups"]:
                    if group["rank"]:
                        sheet.write_number(r, 0, group["rank"], int_fmt)
                    else:
                        sheet.write_blank(r, 0, None, int_fmt)
                    sheet.write_string(r, 1, text(group["label"]), cell_fmt)
                    sheet.write_number(r, 2, group["rows"], int_fmt)
                    sheet.write_number(r, 3, group["points"], num_fmt)
                    sheet.write_number(r, 4, group["share"], pct_fmt)
                    r += 1
            r += 2
        book.close()
        return buffer.getvalue()
