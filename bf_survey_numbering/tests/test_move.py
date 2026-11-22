from odoo.exceptions import UserError
from odoo.tests import new_test_user, tagged

from .test_numbering import NumberingCase


@tagged("post_install", "-at_install")
class TestMove(NumberingCase):

    def move(self, questions, target, placement="end", after=None, user=None):
        Wizard = self.env["bf.survey.question.move"]
        if user:
            Wizard = Wizard.with_user(user)
        wizard = Wizard.create({
            "survey_id": self.survey.id,
            "question_ids": [(6, 0, questions.ids)],
            "target_page_id": target.id,
            "placement": placement,
            "after_question_id": after.id if after else False,
        })
        wizard.action_move()

    def order(self):
        return self.survey.question_and_page_ids.sorted().mapped("title")

    def test_to_the_end_of_another_section(self):
        self.move(self.q11, self.s2)
        self.assertEqual(self.order(), [
            "Your details", "Phone",
            "Your company", "Skilled staff?", "Who are they?", "Founding year", "Company name",
            "Consent", "Signed by"])
        self.assertEqual(self.numbers()["Company name"], "2.3")
        self.assertEqual(self.numbers()["Phone"], "1.1")

    def test_to_the_start_of_another_section_keeps_their_order(self):
        self.move(self.q11 | self.q12, self.s3, "start")
        self.assertEqual(self.order()[-4:], ["Consent", "Company name", "Phone", "Signed by"])

    def test_after_a_question(self):
        self.move(self.q31, self.s1, "after", after=self.q11)
        self.assertEqual(self.order()[:4], ["Your details", "Company name", "Signed by", "Phone"])

    def test_a_section_moves_with_its_questions(self):
        self.move(self.s3, self.s1, "start")
        self.assertEqual(self.order(), [
            "Consent", "Signed by",
            "Your details", "Company name", "Phone",
            "Your company", "Skilled staff?", "Who are they?", "Founding year"])
        self.assertEqual(self.numbers()["Signed by"], "1.1")
        self.assertEqual(self.numbers()["Your details"], "2")

    def test_a_section_cannot_go_after_a_question(self):
        with self.assertRaises(UserError):
            self.move(self.s3, self.s1, "after", after=self.q11)

    def test_a_section_and_stray_questions_are_not_moved_together(self):
        # In one block, the stray question would land before the section's
        # header, in the section above (or outside any section at the top).
        before = self.order()
        for target in (self.s1, self.s2):
            with self.assertRaises(UserError):
                self.move(self.s3 | self.q11, target, "start")
        self.assertEqual(self.order(), before)

    def test_a_section_with_its_own_question_picked_too(self):
        self.move(self.s3 | self.q31, self.s1, "start")
        self.assertEqual(self.order()[:2], ["Consent", "Signed by"])

    def test_responses_in_progress_are_counted(self):
        answer = self.survey._create_answer()
        answer.state = "in_progress"
        wizard = self.env["bf.survey.question.move"].create({"survey_id": self.survey.id})
        self.assertEqual(wizard.answers_in_progress, 1)

    def test_a_conditional_question_cannot_go_before_its_trigger(self):
        before = self.order()
        with self.assertRaises(UserError):
            self.move(self.q211, self.s1)
        self.assertEqual(self.order(), before)

    def test_the_trigger_cannot_go_after_its_conditional_question(self):
        with self.assertRaises(UserError):
            self.move(self.q21, self.s3)

    def test_moving_both_together_is_fine(self):
        self.move(self.q21 | self.q211, self.s3)
        self.assertEqual(self.order()[-3:], ["Signed by", "Skilled staff?", "Who are they?"])
        self.assertEqual(self.numbers()["Who are they?"], "3.2.1")

    def test_sequences_end_distinct(self):
        # Ties were what made a moved question slip back where it came from.
        self.q22.sequence = self.q21.sequence
        self.move(self.q12, self.s2, "start")
        sequences = self.survey.question_and_page_ids.mapped("sequence")
        self.assertEqual(len(sequences), len(set(sequences)))
        self.assertEqual(self.order()[2:4], ["Your company", "Phone"])

    def test_opened_from_the_question_list_on_several(self):
        wizard = self.env["bf.survey.question.move"].with_context(
            active_model="survey.question", active_ids=(self.q11 | self.q12).ids,
        ).create({"target_page_id": self.s3.id})
        self.assertEqual(wizard.survey_id, self.survey)
        self.assertEqual(wizard.question_ids, self.q11 | self.q12)

    def test_several_surveys_at_once_is_refused(self):
        other = self.env["survey.survey"].create({
            "title": "Other",
            "question_and_page_ids": [(0, 0, {"title": "X", "question_type": "char_box"})],
        })
        with self.assertRaises(UserError):
            self.env["bf.survey.question.move"].with_context(
                active_model="survey.question",
                active_ids=(self.q11 | other.question_and_page_ids).ids,
            ).default_get(["question_ids", "survey_id"])

    def test_a_colleague_does_not_read_my_form(self):
        alice = new_test_user(self.env, login="bf_move_alice",
                              groups="base.group_user,survey.group_survey_user")
        bob = new_test_user(self.env, login="bf_move_bob",
                            groups="base.group_user,survey.group_survey_user")
        mine = self.env["bf.survey.question.move"].with_user(alice).create({
            "survey_id": self.survey.id, "target_page_id": self.s2.id,
        })
        seen = self.env["bf.survey.question.move"].with_user(bob).search([("id", "=", mine.id)])
        self.assertFalse(seen)

    def test_a_survey_officer_can_move(self):
        officer = new_test_user(self.env, login="bf_move_officer",
                                groups="base.group_user,survey.group_survey_user")
        # The core lets a survey officer edit any survey not restricted to
        # others; making them responsible keeps the test true either way.
        self.survey.user_id = officer
        self.move(self.q11, self.s2, user=officer)
        self.assertEqual(self.numbers()["Company name"], "2.3")
