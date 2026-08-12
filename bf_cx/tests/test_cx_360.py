"""Internal 360: subject filing, respondent masking, honest aggregates.

Three things separate a real 360 from a survey wearing the label, and
each one is a way to hurt someone if it breaks: the entry must be filed
under the person REVIEWED (not the respondent), the respondent must not
land in the registry when the program says to mask them, and a per-person
average must refuse to print itself on two answers.
"""
from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged

from odoo.addons.bf_cx.models.bf_cx_feedback import MIN_360_RESPONSES


@tagged("post_install", "-at_install")
class TestCx360(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.program = cls.env.ref("bf_cx.program_360_default")
        cls.env["ir.config_parameter"].sudo().set_param(
            "bf_cx.solicitation_cooldown_days", "0"
        )
        cls.subject = cls.env["res.users"].create(
            {
                "name": "Personne évaluée",
                "login": "cx-360-subject",
                "email": "subject-360@example.com",
            }
        )
        cls.reviewers = cls.env["res.partner"].create(
            [
                {"name": "Pair %s" % i, "email": "pair%s-360@example.com" % i}
                for i in range(4)
            ]
        )

    def _wave(self, subject=None, partners=None):
        return self.env["bf.cx.wave"].create(
            {
                "name": "Ronde 360",
                "program_id": self.program.id,
                "subject_user_id": (subject or self.subject).id,
                "partner_ids": [(6, 0, (partners or self.reviewers).ids)],
            }
        )

    def _answer(self, user_input, score, comment="Exemple concret."):
        user_input._save_lines(self.program.score_question_id, str(score))
        if comment:
            user_input._save_lines(self.program.comment_question_id, comment)
        user_input._mark_done()

    # ------------------------------------------------------------------
    # Filing
    # ------------------------------------------------------------------
    def test_answer_is_filed_under_the_person_reviewed(self):
        wave = self._wave()
        wave.action_send()
        self._answer(wave.user_input_ids[0], 8)
        feedback = self.env["bf.cx.feedback"].search(
            [("survey_user_input_id", "=", wave.user_input_ids[0].id)]
        )
        self.assertEqual(len(feedback), 1)
        self.assertEqual(feedback.kind, "internal")
        self.assertEqual(
            feedback.subject_user_id,
            self.subject,
            "an internal answer belongs to the person reviewed",
        )

    def test_respondent_is_masked_when_the_program_says_so(self):
        self.assertTrue(self.program.hide_respondent)
        wave = self._wave()
        wave.action_send()
        answer = wave.user_input_ids[0]
        self._answer(answer, 9)
        feedback = self.env["bf.cx.feedback"].search(
            [("survey_user_input_id", "=", answer.id)]
        )
        self.assertFalse(
            feedback.partner_id,
            "the registry must not name the respondent when masked",
        )
        self.assertIn("Anonyme", feedback.display_name)

    def test_respondent_is_kept_when_masking_is_off(self):
        self.program.hide_respondent = False
        wave = self._wave()
        wave.action_send()
        answer = wave.user_input_ids[0]
        self._answer(answer, 7)
        feedback = self.env["bf.cx.feedback"].search(
            [("survey_user_input_id", "=", answer.id)]
        )
        self.assertEqual(feedback.partner_id, answer.partner_id)

    def test_client_program_cannot_carry_a_subject(self):
        """A subject on a client program would file client answers under
        an employee's name."""
        with self.assertRaises(ValidationError):
            self.env["bf.cx.wave"].create(
                {
                    "name": "Vague client",
                    "program_id": self.env.ref("bf_cx.program_nps_default").id,
                    "subject_user_id": self.subject.id,
                }
            )

    # ------------------------------------------------------------------
    # Aggregates
    # ------------------------------------------------------------------
    def _seed(self, scores):
        Feedback = self.env["bf.cx.feedback"]
        for score in scores:
            Feedback.create(
                {
                    "kind": "internal",
                    "score": score,
                    "score_max": 10,
                    "source": "manual",
                    "program_id": self.program.id,
                    "subject_user_id": self.subject.id,
                }
            )

    def test_average_is_masked_under_the_threshold(self):
        self._seed([8, 6])
        summary = self.env["bf.cx.feedback"]._bf_cx_360_summary(
            [("subject_user_id", "=", self.subject.id)]
        )[self.subject.id]
        self.assertEqual(summary["n"], 2)
        self.assertTrue(summary["masked"])
        display = self.env["bf.cx.feedback"]._bf_cx_360_display(summary)
        self.assertNotIn("7", display, "a masked average must not leak")

    def test_average_appears_at_the_threshold(self):
        self._seed([8, 6, 10])
        summary = self.env["bf.cx.feedback"]._bf_cx_360_summary(
            [("subject_user_id", "=", self.subject.id)]
        )[self.subject.id]
        self.assertEqual(summary["n"], MIN_360_RESPONSES)
        self.assertFalse(summary["masked"])
        self.assertEqual(summary["average"], 8.0)
        self.assertIn("8.0/10", self.env["bf.cx.feedback"]._bf_cx_360_display(summary))

    def test_client_feedback_never_counts_in_a_360(self):
        self._seed([9, 9, 9])
        partner = self.env["res.partner"].create(
            {"name": "Client", "email": "client-360@example.com"}
        )
        self.env["bf.cx.feedback"].create(
            {
                "partner_id": partner.id,
                "kind": "nps",
                "score": 0,
                "score_max": 10,
                "source": "manual",
                "subject_user_id": self.subject.id,
            }
        )
        summary = self.env["bf.cx.feedback"]._bf_cx_360_summary(
            [("subject_user_id", "=", self.subject.id)]
        )[self.subject.id]
        self.assertEqual(summary["n"], 3, "only internal entries count")
        self.assertEqual(summary["average"], 9.0)

    def test_wave_summary_is_scoped_to_its_own_round(self):
        """Two rounds on the same person must not blend into one figure."""
        first = self._wave()
        first.action_send()
        for answer, score in zip(first.user_input_ids, (10, 10, 10, 10)):
            self._answer(answer, score)
        second = self._wave()
        second.action_send()
        for answer in second.user_input_ids:
            self._answer(answer, 4)
        self.assertIn("10.0/10", first.subject_summary)
        self.assertIn("4.0/10", second.subject_summary)

    def test_360_never_triggers_the_client_closed_loop(self):
        """A low 360 score is a conversation, not a service incident."""
        wave = self._wave()
        wave.action_send()
        self._answer(wave.user_input_ids[0], 1)
        feedback = self.env["bf.cx.feedback"].search(
            [("survey_user_input_id", "=", wave.user_input_ids[0].id)]
        )
        self.assertFalse(feedback.needs_followup)
        self.assertFalse(
            self.env["mail.activity"].search(
                [
                    ("res_model", "=", "bf.cx.feedback"),
                    ("res_id", "=", feedback.id),
                ]
            ),
            "no closed-loop activity may be raised on internal feedback",
        )

    def test_360_does_not_burn_the_client_solicitation_budget(self):
        wave = self._wave()
        wave.action_send()
        self.assertFalse(
            self.reviewers.filtered("bf_cx_last_solicited"),
            "an internal round must not consume the client cooldown",
        )
