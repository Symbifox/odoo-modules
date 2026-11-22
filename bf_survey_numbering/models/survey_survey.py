from collections import defaultdict

from odoo import api, fields, models
from odoo.exceptions import UserError


def _key(record):
    """One identity for a question, saved or not.

    In the survey form the lines are new records wrapping the saved ones, and
    a trigger can come back as either: compare what they stand for.
    """
    return record._origin.id or record.id


def in_order(lines):
    """A survey's lines in the order the respondent meets them: sequence, then id.

    Spelled out rather than left to `sorted()`: on the lines of a form being
    edited (new records), `sorted()` keeps the order they came in and ignores
    the sequence just dragged. A line not saved yet comes after the saved ones
    of the same sequence, as its id will.
    """
    return lines.sorted(lambda q: (q.sequence, 0 if q._origin.id else 1, q._origin.id or 0))


class SurveySurvey(models.Model):
    _inherit = "survey.survey"

    bf_numbering = fields.Boolean(
        string="Number sections and questions",
        help="Show a number before every section (1, 2, 3...) and every "
             "question (2.1, 2.2...), in the editor, the form the respondent "
             "fills in, the printed page and the results. A conditional "
             "question shown by an earlier question of the same section is "
             "numbered under it (2.12.1). Numbers follow the order of the "
             "questions: move one and the others renumber. The titles "
             "themselves are not changed.",
    )
    bf_typed_number_count = fields.Integer(
        string="Titles with a typed number",
        compute="_compute_bf_typed_number_count",
        help="Sections and questions whose title already starts with a "
             "number typed by hand, which numbering would show twice.",
    )

    @api.depends("question_and_page_ids.title")
    def _compute_bf_typed_number_count(self):
        for survey in self:
            survey.bf_typed_number_count = len(
                survey.question_and_page_ids.filtered(lambda q: q._bf_typed_number()))

    def _bf_numbers(self):
        """{question id: number} for the whole survey, in its current order.

        A section counts 1, 2, 3; a question counts within its section (2.1,
        2.2); a conditional question whose trigger is an earlier question of
        the same section is numbered under that trigger (2.12.1), and does not
        take a number of the section. Questions placed before the first section
        count 1, 2, 3 by themselves.
        """
        self.ensure_one()
        if not self.bf_numbering:
            return {}
        numbers = {}
        section = 0
        rank = 0
        children = defaultdict(int)
        in_section = []  # questions of the current section, in order
        for question in in_order(self.question_and_page_ids):
            if question.is_page:
                section += 1
                rank = 0
                in_section = []
                numbers[_key(question)] = str(section)
                continue
            triggers = {_key(q) for q in question.triggering_answer_ids.question_id}
            parent = next((q for q in reversed(in_section) if _key(q) in triggers), None)
            if parent is not None:
                children[parent] += 1
                numbers[_key(question)] = f"{numbers[_key(parent)]}.{children[parent]}"
            else:
                rank += 1
                numbers[_key(question)] = f"{section}.{rank}" if section else str(rank)
            in_section.append(question)
        return numbers

    def action_bf_strip_typed_numbers(self):
        """Remove the numbers typed by hand that repeat the survey's own numbers.

        Only a typed number EQUAL to the line's computed number is removed
        ("2.1 Founding year" on question 2.1): that is the duplicate numbering
        would show. A title that merely starts like a number ("2.5 hours a
        week", on question 3.4) is not a duplicate and is left alone, and so is
        a typed number that no longer matches the line's place: those are
        named to the user, to be fixed by hand. The removal cannot be undone,
        so it never guesses.

        Every installed language, and the en_US source, is cleaned on its own
        through the translation API: writing the title in one language must not
        overwrite the others with it.
        """
        self.ensure_one()
        if not self.bf_numbering:
            raise UserError(self.env._(
                "Tick “Number sections and questions” first: only the typed numbers "
                "that repeat the survey's own numbers are removed."))
        # en_US is the source value, kept even where English is not installed
        # (a database may run in fr_CA only): it is what a script reading the survey
        # without a language gets, so it is cleaned too.
        langs = ["en_US"] + [code for code, _name in self.env["res.lang"].get_installed()
                             if code != "en_US"]
        kept = self.env["survey.question"]
        for question in self.question_and_page_ids:
            updates = {}
            for lang in langs:
                typed = question._bf_typed_number(lang)
                if not typed:
                    continue
                title = question.with_context(lang=lang).title
                rest = title[len(typed):]
                if typed.strip().rstrip(".") != question.bf_number:
                    kept |= question
                elif rest.strip():
                    updates[lang] = rest
                # else: the title IS its number; emptying it would leave a
                # blank line, so it stays as typed, unnamed (it is in place).
            if updates:
                question.update_field_translations("title", updates)
        reload = {"type": "ir.actions.client", "tag": "soft_reload"}
        if not kept:
            return reload
        names = "\n".join(f"• {q.bf_number} : {q.title}" for q in kept[:10])
        more = len(kept) - 10
        if more > 0:
            names += "\n" + self.env._("… and %s more.", more)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": self.env._("Some typed numbers were kept"),
                "message": self.env._(
                    "These titles start with a number that is not their place in the "
                    "survey, so it was left as typed. Correct them by hand:\n%s", names),
                "type": "warning",
                "sticky": True,
                "next": reload,
            },
        }

    def action_bf_move_questions(self):
        self.ensure_one()
        return self.env["bf.survey.question.move"]._bf_action(self)
