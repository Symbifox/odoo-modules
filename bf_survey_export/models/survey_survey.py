import io
import re

import xlsxwriter
from lxml import etree, html

from odoo import fields, models
from odoo.tools import format_date

# What each kind of question shows the respondent. Odoo keeps the choices, the
# rows and the comment box of a question whose type is changed afterwards, and
# hides them by type when it renders: the workbook must hide them the same way.
CHOICE_TYPES = {"simple_choice", "multiple_choice", "matrix"}
ROW_TYPES = {"matrix", "percent_allocation"}
COMMENT_AS_CHOICE_TYPES = {"simple_choice", "multiple_choice"}
# The only types a condition can hang on, and the ones that show a placeholder.
TRIGGER_TYPES = {"simple_choice", "multiple_choice"}
PLACEHOLDER_TYPES = {"char_box", "text_box", "numerical_box", "date", "datetime"}
# An item number or bullet left alone on its line, its text on the next one
# (a list item that holds a paragraph, as pasted from a word processor).
_LONE_MARKER = re.compile(r"^(?:\d+\.|•)$")

# Help texts are HTML; the workbook wants their words and their line breaks.
_BLOCKS = {
    "p",
    "div",
    "br",
    "li",
    "tr",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "blockquote",
    "pre",
    "ul",
    "ol",
    "table",
}
_CELLS = {"td", "th"}
# Characters a file name cannot carry on at least one of the systems it will be
# saved on, and the invisible ones that reorder how a name is displayed.
_UNSAFE_FILENAME = re.compile(
    r'[\\/:*?"<>|\x00-\x1f\x7f'
    r"\u200e\u200f\u202a-\u202e\u2066-\u2069]+"
)
# The longest string a spreadsheet cell holds; xlsxwriter cuts longer ones
# without saying so.
_CELL_MAX = 32767


def _plain(value):
    """The text of an HTML help text: one paragraph, list item or table row
    per line, numbered lists numbered, cells separated by " | "."""
    if not value or not str(value).strip():
        return ""
    try:
        root = html.fragment_fromstring(str(value), create_parent="div")
    except (etree.ParserError, ValueError):
        return str(value).strip()
    out = []

    def walk(node, counter=None):
        tag = node.tag if isinstance(node.tag, str) else ""
        if tag == "li":
            if counter is not None:
                counter[0] += 1
                out.append(f"\n{counter[0]}. ")
            else:
                out.append("\n• ")
        elif tag in _BLOCKS:
            out.append("\n")
        if tag and node.text:
            out.append(node.text)
        child_counter = [0] if tag == "ol" else (None if tag == "ul" else counter)
        for child in node:
            walk(child, child_counter)
        if tag in _CELLS:
            out.append(" | ")
        elif tag in _BLOCKS:
            out.append("\n")
        if node.tail:
            out.append(node.tail)

    walk(root)
    text = "".join(out).replace("\xa0", " ")
    lines = [line.strip().removesuffix("|").strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    merged = []
    for line in lines:
        if merged and _LONE_MARKER.match(merged[-1]):
            merged[-1] = f"{merged[-1]} {line}"
        else:
            merged.append(line)
    return "\n".join(merged)


class SurveySurvey(models.Model):
    _inherit = "survey.survey"

    bf_pdf_theme_id = fields.Many2one(
        "bf.survey.pdf.theme",
        string="PDF theme",
        help="Colours, logo and font of this survey's PDF exports. "
        "Empty: the company's colours and logo.",
    )

    def action_bf_export_questions_pdf(self):
        """Print the blank questionnaire of this survey."""
        self.ensure_one()
        return self.env.ref(
            "bf_survey_export.action_report_bf_survey_questions"
        ).report_action(self)

    def _bf_pdf_filename(self):
        """The PDF's name, without its extension (Odoo adds it)."""
        self.ensure_one()
        return self._bf_questions_filename().removesuffix(".xlsx")

    def _bf_pdf_style(self):
        self.ensure_one()
        return self.env["bf.survey.pdf.theme"]._bf_resolve(self.bf_pdf_theme_id)

    def _bf_pdf_counts(self, rows):
        """ "2 sections · 14 questions", under the title of the blank PDF."""
        sections = sum(1 for r in rows if r["section"])
        questions = len(rows) - sections
        parts = []
        if sections:
            parts.append(self.env._("%(count)s section(s)", count=sections))
        parts.append(self.env._("%(count)s question(s)", count=questions))
        return " · ".join(parts)

    @staticmethod
    def _bf_pdf_blocks(rows):
        """The rows of the blank PDF in unbreakable blocks: a question alone,
        or a section with its first question, so that no section title is
        left alone at the foot of a page."""
        blocks = []
        for row in rows:
            if row["section"]:
                blocks.append({"section": row, "question": None})
            elif blocks and blocks[-1]["question"] is None:
                blocks[-1]["question"] = row
            else:
                blocks.append({"section": None, "question": row})
        return blocks

    def _bf_pdf_numbered_title(self, line):
        """A section or question title with its number, when the survey
        numbers its questions (bf_survey_numbering)."""
        num = line.bf_number if "bf_number" in line._fields else ""
        if not num:
            return line.title or ""
        return (
            f"{num}. {line.title or ''}"
            if line.is_page
            else f"{num} {line.title or ''}"
        )

    def action_bf_export_questions_xlsx(self):
        """Download the questions workbook of this survey."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_url",
            "url": f"/bf_survey_export/questions/{self.id}",
            "target": "download",
        }

    def _bf_questions_filename(self):
        self.ensure_one()
        title = " ".join(_UNSAFE_FILENAME.sub(" ", self.title or "").split())
        title = title.strip(" .-")[:150].strip(" .-")
        return f"{title or self.env._('Survey')} - {self.env._('questions')}.xlsx"

    # ------------------------------------------------------------------
    # Reading the survey
    # ------------------------------------------------------------------

    def _bf_questions_rows(self):
        """The survey's lines in its own order, as plain values.

        A section is ``{"section": True, ...}``; a question carries everything
        the workbook shows. Nothing here reads a response: the workbook is the
        questionnaire, not its results. ``is_correct``, ``answer_score`` and the
        ``answer_*`` fields are never read: they are the answer key.
        """
        self.ensure_one()
        _ = self.env._
        Question = self.env["survey.question"]
        types = dict(Question._fields["question_type"]._description_selection(self.env))
        subtypes = dict(
            Question._fields["matrix_subtype"]._description_selection(self.env)
        )
        # bf_survey_numbering, when installed, computes the numbers; without
        # it the numbers, if any, are typed in the titles and stay there.
        numbered = "bf_number" in Question._fields

        def number(line):
            return (line.bf_number or "") if numbered else ""

        def section_title(page):
            if not page:
                return ""
            num = number(page)
            return f"{num}. {page.title or ''}" if num else (page.title or "")

        lines = self.question_and_page_ids
        # Conditions as Odoo applies them. A survey that draws its questions
        # at random ignores them all. One question per page: Odoo's own
        # verdict (_get_pages_and_questions_to_show) voids a trigger placed
        # after its question. One page, or one page per section: the browser
        # shows the question as soon as a trigger is ticked on the same page,
        # in any order; a trigger on a later page comes too late.
        random = self.questions_selection == "random"
        per_question = self.questions_layout == "page_per_question"
        shown = self._get_pages_and_questions_to_show() if per_question else lines
        one_page = self.questions_layout == "one_page"

        def valid_trigger(source, line):
            if source.is_page or source.question_type not in TRIGGER_TYPES:
                return False
            before = (source.sequence, source.id) < (line.sequence, line.id)
            if per_question:
                return source in shown and before
            return one_page or before or source.page_id == line.page_id

        rows = []
        for line in lines:
            if line.is_page:
                note = ""
                in_section = lines.filtered(lambda q, p=line: q.page_id == p)
                if (
                    self.questions_selection == "random"
                    and 0 < line.random_questions_count < len(in_section)
                ):
                    note = _(
                        "%(count)s question(s) drawn at random from this section",
                        count=line.random_questions_count,
                    )
                rows.append(
                    {
                        "section": True,
                        "number": number(line),
                        "title": line.title or "",
                        "description": _plain(line.description),
                        "note": note,
                    }
                )
                continue

            qtype = line.question_type
            kind = types.get(qtype, qtype or "")
            if qtype == "matrix" and line.matrix_subtype:
                kind = (
                    f"{kind} ({subtypes.get(line.matrix_subtype, line.matrix_subtype)})"
                )

            answers, specify = self._bf_question_answers(line)
            condition = ""
            if line.triggering_answer_ids and not random:
                condition = self._bf_question_condition(
                    line, number, valid_trigger, line in shown
                )

            rows.append(
                {
                    "section": False,
                    "qtype": qtype,
                    "multiple": qtype == "multiple_choice"
                    or (qtype == "matrix" and line.matrix_subtype == "multiple"),
                    "page": section_title(line.page_id),
                    "number": number(line),
                    "title": line.title or "",
                    "type": kind,
                    "grid": qtype in ROW_TYPES,
                    "mandatory": line.constr_mandatory,
                    "description": self._bf_question_description(line),
                    "answers": answers,
                    "rows": [r.value or "" for r in line.matrix_row_ids]
                    if qtype in ROW_TYPES
                    else [],
                    "specify": specify,
                    "condition": condition,
                }
            )
        return rows

    def _bf_question_answers(self, line):
        """The possible answers of a question as the respondent sees them,
        and the label of its "please specify" box ("" when it has none)."""
        _ = self.env._
        qtype = line.question_type
        answers = []
        if qtype in CHOICE_TYPES:
            answers = [a.value or _("(image)") for a in line.suggested_answer_ids]
        elif qtype == "scale":
            answers = [str(v) for v in range(line.scale_min, line.scale_max + 1)]
        if not (qtype in CHOICE_TYPES and line.comments_allowed):
            return answers, ""
        message = line.comments_message or _("If other, please specify:")
        if line.comment_count_as_answer and qtype in COMMENT_AS_CHOICE_TYPES:
            # Odoo shows the comment as one more choice, with a text box.
            answers.append(message)
        return answers, message

    def _bf_question_description(self, line):
        """The help text, the placeholder and the scale labels shown."""
        _ = self.env._
        qtype = line.question_type
        extras = [_plain(line.description)]
        if qtype in PLACEHOLDER_TYPES and line.question_placeholder:
            extras.append(_("Placeholder: %(text)s", text=line.question_placeholder))
        if qtype == "scale":
            # Odoo shows these under the scale, at its left, centre and right,
            # without tying them to a value.
            anchors = [
                (_("Left"), line.scale_min_label),
                (_("Centre"), line.scale_mid_label),
                (_("Right"), line.scale_max_label),
            ]
            labels = "; ".join(f"{side}: {text}" for side, text in anchors if text)
            if labels:
                extras.append(_("Scale labels: %(labels)s", labels=labels))
        return "\n".join(t for t in extras if t)

    def _bf_question_condition(self, line, number, valid_trigger, can_show):
        """When a conditional question is shown, or that it never is."""
        _ = self.env._
        conditions = []
        for answer in line.triggering_answer_ids:
            source = answer.question_id
            if not valid_trigger(source, line):
                continue
            label = number(source) or _("“%(title)s”", title=source.title or "")
            conditions.append(
                _(
                    "If %(question)s = “%(answer)s”",
                    question=label,
                    answer=answer.value or "",
                )
            )
        if not can_show or not conditions:
            return _(
                "Never shown: none of its triggers can show it (placed too late, "
                "or not a choice question)"
            )
        return f"\n{_('or')}\n".join(conditions)

    # ------------------------------------------------------------------
    # Writing the workbook
    # ------------------------------------------------------------------

    def _bf_questions_workbook(self):
        """The questions workbook of this survey, as bytes (.xlsx)."""
        self.ensure_one()
        _ = self.env._
        rows = self._bf_questions_rows()
        with_numbers = any(r["number"] for r in rows)
        truncated = _("[truncated]")

        def text(value):
            value = value or ""
            if len(value) > _CELL_MAX:
                value = value[: _CELL_MAX - len(truncated) - 2] + "… " + truncated
            return value

        buffer = io.BytesIO()
        # Every cell is written with write_string: a title that starts with
        # "=" or "http" is text a person typed, never a formula nor a link.
        book = xlsxwriter.Workbook(
            buffer,
            {
                "in_memory": True,
                "strings_to_formulas": False,
                "strings_to_urls": False,
                "strings_to_numbers": False,
            },
        )
        title_fmt = book.add_format({"bold": True, "font_size": 14})
        note_fmt = book.add_format({"italic": True, "font_color": "#555555"})
        border = {"border": 1, "border_color": "#BFBFBF"}
        head_fmt = book.add_format(
            {
                "bold": True,
                "font_color": "#FFFFFF",
                "bg_color": "#1F3864",
                "text_wrap": True,
                "valign": "vcenter",
                **border,
            }
        )
        cell_fmt = book.add_format({"text_wrap": True, "valign": "top", **border})
        section_fmt = book.add_format(
            {
                "bold": True,
                "text_wrap": True,
                "valign": "top",
                "bg_color": "#D9E1F2",
                **border,
            }
        )

        # Questions sheet
        sheet = book.add_worksheet(_("Questions")[:31])
        columns = [("section", _("Section"), 24)]
        if with_numbers:
            columns.append(("number", _("No."), 8))
        columns += [
            ("title", _("Question"), 50),
            ("type", _("Answer type"), 22),
            ("mandatory", _("Mandatory"), 11),
            ("description", _("Help text shown"), 45),
            ("answers", _("Possible answers (or matrix columns)"), 42),
            ("rows", _("Rows (matrix or allocation)"), 38),
            ("specify", _("“Please specify” box"), 30),
            ("condition", _("Shown when"), 24),
        ]
        keys = [key for key, _label, _width in columns]
        sheet.write_string(0, 0, text(self.title), title_fmt)
        sheet.write_string(
            1,
            0,
            _(
                "Questions and possible answers, without any respondent's answers. "
                "Exported on %(date)s.",
                date=format_date(self.env, fields.Date.context_today(self)),
            ),
            note_fmt,
        )
        header = 3
        for col, (_key, label, width) in enumerate(columns):
            sheet.write_string(header, col, label, head_fmt)
            sheet.set_column(col, col, width)

        def boxes(values):
            return "\n".join(f"☐ {v}" for v in values)

        r = header
        for row in rows:
            r += 1
            if row["section"]:
                title = (
                    f"{row['number']}. {row['title']}"
                    if row["number"]
                    else row["title"]
                )
                values = dict.fromkeys(keys, "")
                values["section"] = title
                # The section's help text goes where a question's does.
                values["description"] = "\n".join(
                    t for t in (row["description"], row["note"]) if t
                )
                for col, key in enumerate(keys):
                    sheet.write_string(r, col, text(values[key]), section_fmt)
                continue
            values = {
                "section": row["page"],
                "number": row["number"],
                "title": row["title"],
                "type": row["type"],
                "mandatory": _("Yes") if row["mandatory"] else _("No"),
                "description": row["description"],
                "answers": boxes(row["answers"]),
                "rows": boxes(row["rows"]),
                "specify": row["specify"],
                "condition": row["condition"],
            }
            for col, key in enumerate(keys):
                sheet.write_string(r, col, text(values[key]), cell_fmt)
        sheet.freeze_panes(header + 1, keys.index("title"))
        sheet.autofilter(header, 0, max(r, header), len(columns) - 1)
        sheet.set_landscape()
        sheet.fit_to_pages(1, 0)
        sheet.repeat_rows(header)

        # Possible answers sheet: one line per answer, to filter or sort.
        answers = book.add_worksheet(_("Possible answers")[:31])
        cols = [(_("Section"), 24)]
        cols += [(_("No."), 8)] if with_numbers else []
        cols += [(_("Question"), 60), (_("Role"), 12), (_("Possible answer"), 70)]
        for col, (label, width) in enumerate(cols):
            answers.write_string(0, col, label, head_fmt)
            answers.set_column(col, col, width)
        r = 0
        for row in rows:
            if row["section"]:
                continue
            role = _("Column") if row["grid"] else _("Choice")
            lines = [(role, a) for a in row["answers"]]
            lines += [(_("Row"), v) for v in row["rows"]]
            for role_label, value in lines:
                r += 1
                values = [row["page"]] + ([row["number"]] if with_numbers else [])
                values += [row["title"], role_label, value]
                for col, cell in enumerate(values):
                    answers.write_string(r, col, text(cell))
        answers.freeze_panes(1, 0)
        answers.autofilter(0, 0, max(r, 1), len(cols) - 1)

        book.close()
        return buffer.getvalue()
