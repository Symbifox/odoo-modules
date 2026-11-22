from odoo.tests import TransactionCase, tagged


class NumberingCase(TransactionCase):
    """A survey shaped like a real diagnostic questionnaire: no question
    before the first section, and a conditional question shown by an earlier
    question of its section (2.12 → 2.12.1)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.survey = cls.env["survey.survey"].create({
            "title": "Diagnostic",
            "bf_numbering": True,
        })
        Q = cls.env["survey.question"]
        cls.s1 = cls._line("Your details", 10, page=True)
        cls.q11 = cls._line("Company name", 20)
        cls.q12 = cls._line("Phone", 30)
        cls.s2 = cls._line("Your company", 40, page=True)
        cls.q21 = cls._line("Skilled staff?", 50, kind="simple_choice",
                            answers=["Yes", "No"])
        cls.q211 = cls._line("Who are they?", 60)
        cls.q211.triggering_answer_ids = cls.q21.suggested_answer_ids[:1]
        cls.q22 = cls._line("Founding year", 70)
        cls.s3 = cls._line("Consent", 80, page=True)
        cls.q31 = cls._line("Signed by", 90)
        cls.all = Q.browse([q.id for q in (cls.s1, cls.q11, cls.q12, cls.s2, cls.q21,
                                           cls.q211, cls.q22, cls.s3, cls.q31)])

    @classmethod
    def _line(cls, title, sequence, page=False, kind="char_box", answers=()):
        # survey.question.create cannot be called with a list from RPC, but it
        # can from Python; writing through the survey keeps one path anyway.
        vals = {"title": title, "sequence": sequence, "is_page": page}
        if not page:
            vals["question_type"] = kind
            if answers:
                vals["suggested_answer_ids"] = [(0, 0, {"value": a}) for a in answers]
        cls.survey.write({"question_and_page_ids": [(0, 0, vals)]})
        return cls.survey.question_and_page_ids.filtered(lambda q: q.title == title)

    def numbers(self):
        self.all.invalidate_recordset(["bf_number"])
        return {q.title: q.bf_number for q in self.all}


@tagged("post_install", "-at_install")
class TestNumbering(NumberingCase):

    def test_numbers_follow_sections_and_triggers(self):
        self.assertEqual(self.numbers(), {
            "Your details": "1", "Company name": "1.1", "Phone": "1.2",
            "Your company": "2", "Skilled staff?": "2.1", "Who are they?": "2.1.1",
            "Founding year": "2.2",
            "Consent": "3", "Signed by": "3.1",
        })

    def test_off_by_default_and_when_unticked(self):
        other = self.env["survey.survey"].create({
            "title": "Not numbered",
            "question_and_page_ids": [(0, 0, {"title": "S", "is_page": True}),
                                      (0, 0, {"title": "Q", "question_type": "char_box"})],
        })
        self.assertFalse(other.bf_numbering)
        self.assertEqual(other.question_and_page_ids.mapped("bf_number"), [False, False])
        self.survey.bf_numbering = False
        self.assertFalse(any(self.numbers().values()))

    def test_moving_a_question_renumbers_the_others(self):
        # "Phone" goes to the end of section 2: section 1 has one question left.
        self.q12.sequence = 75
        numbers = self.numbers()
        self.assertEqual(numbers["Company name"], "1.1")
        self.assertEqual(numbers["Phone"], "2.3")
        self.assertEqual(numbers["Founding year"], "2.2")

    def test_ties_on_sequence_follow_the_ids(self):
        # A real questionnaire had three questions at sequence 172: the order
        # (and so the numbers) must be the one the respondent sees, by id.
        self.q22.sequence = self.q21.sequence
        numbers = self.numbers()
        self.assertEqual(numbers["Skilled staff?"], "2.1")
        self.assertEqual(numbers["Founding year"], "2.2")
        self.assertEqual(numbers["Who are they?"], "2.1.1")

    def test_trigger_in_another_section_is_not_a_parent(self):
        self.q31.triggering_answer_ids = self.q21.suggested_answer_ids[:1]
        self.assertEqual(self.numbers()["Signed by"], "3.1")

    def test_questions_before_any_section(self):
        survey = self.env["survey.survey"].create({
            "title": "Flat", "bf_numbering": True,
            "question_and_page_ids": [
                (0, 0, {"title": "A", "question_type": "char_box", "sequence": 1}),
                (0, 0, {"title": "B", "question_type": "char_box", "sequence": 2}),
            ],
        })
        self.assertEqual(survey.question_and_page_ids.mapped("bf_number"), ["1", "2"])

    def test_display_name_carries_the_number(self):
        # What the conditional-display picker and the answer lines show.
        self.assertEqual(self.q211.display_name, "2.1.1 Who are they?")
        self.assertEqual(self.s2.display_name, "2. Your company")
        self.survey.bf_numbering = False
        self.q211.invalidate_recordset(["bf_number", "display_name"])
        self.assertEqual(self.q211.display_name, "Who are they?")

    def test_numbers_follow_the_form_being_edited(self):
        """In the editor the lines are new records: a dragged line must be
        numbered from its new place, not from the order saved in the database
        (`sorted()` alone keeps new records in the order they came in)."""
        from odoo import Command
        # What the survey's onchange does with a dropped handle: a new record
        # holding the saved lines, then the client's command on top.
        form = self.survey.new({"bf_numbering": True,
                                "question_and_page_ids": self.survey.question_and_page_ids},
                               origin=self.survey)
        form._update_cache({"question_and_page_ids": [
            Command.update(self.q12.id, {"sequence": 75})]})
        lines = {q.title: q for q in form.question_and_page_ids}
        self.assertEqual(lines["Phone"].survey_id, form)
        self.assertEqual(lines["Phone"].bf_number, "2.3")
        self.assertEqual(lines["Founding year"].bf_number, "2.2")
        # Nothing was written: the database keeps the old order.
        self.assertEqual(self.numbers()["Phone"], "1.2")


@tagged("post_install", "-at_install")
class TestTypedNumbers(NumberingCase):

    def test_typed_numbers_are_counted_and_removed(self):
        self.s1.title = "1. Your details"
        self.q11.title = "1.1 Company name"
        self.q211.title = "2.1.1 Who are they?"
        self.q22.title = "2026 founding year"  # a figure, not a typed number
        self.survey.invalidate_recordset(["bf_typed_number_count"])
        self.assertEqual(self.survey.bf_typed_number_count, 3)

        action = self.survey.action_bf_strip_typed_numbers()
        self.assertEqual(action["tag"], "soft_reload")
        self.all.invalidate_recordset(["title"])
        self.assertEqual(self.s1.title, "Your details")
        self.assertEqual(self.q11.title, "Company name")
        self.assertEqual(self.q211.title, "Who are they?")
        self.assertEqual(self.q22.title, "2026 founding year")
        self.survey.invalidate_recordset(["bf_typed_number_count"])
        self.assertEqual(self.survey.bf_typed_number_count, 0)

    def test_a_number_that_is_not_the_line_s_own_is_kept_and_named(self):
        # Question 2.2 whose title happens to start with a decimal, and
        # question 1.2 carrying a stale typed number: neither is a duplicate.
        self.q22.title = "2.5 hours a week, is it enough?"
        self.q12.title = "1.9 Phone"
        action = self.survey.action_bf_strip_typed_numbers()
        self.all.invalidate_recordset(["title"])
        self.assertEqual(self.q22.title, "2.5 hours a week, is it enough?")
        self.assertEqual(self.q12.title, "1.9 Phone")
        self.assertEqual(action["tag"], "display_notification")
        self.assertIn("2.2 : 2.5 hours a week", action["params"]["message"])
        self.assertIn("1.2 : 1.9 Phone", action["params"]["message"])

    def test_removal_needs_the_numbering_on(self):
        from odoo.exceptions import UserError
        self.survey.bf_numbering = False
        self.q11.title = "1.1 Company name"
        with self.assertRaises(UserError):
            self.survey.action_bf_strip_typed_numbers()

    def test_each_language_is_cleaned_on_its_own(self):
        self.env["res.lang"]._activate_lang("fr_CA")
        self.q11.title = "1.1 Company name"
        self.q11.update_field_translations("title", {"fr_CA": "1.1 Nom de l'entreprise"})
        self.q12.update_field_translations("title", {"fr_CA": "Téléphone"})
        self.survey.action_bf_strip_typed_numbers()
        self.all.invalidate_recordset(["title"])
        self.assertEqual(self.q11.with_context(lang="en_US").title, "Company name")
        self.assertEqual(self.q11.with_context(lang="fr_CA").title, "Nom de l'entreprise")
        # Untouched: no typed number, in either language.
        self.assertEqual(self.q12.with_context(lang="en_US").title, "Phone")
        self.assertEqual(self.q12.with_context(lang="fr_CA").title, "Téléphone")

    def test_a_title_that_is_only_a_number_is_kept(self):
        self.q12.title = "1.2 "
        self.survey.action_bf_strip_typed_numbers()
        self.q12.invalidate_recordset(["title"])
        self.assertEqual(self.q12.title, "1.2 ")
