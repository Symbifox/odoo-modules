"""The two "yes" of the NPS testimonial question.

"Contact me to confirm" only flags a candidate: nothing may be published
before the call. "Quote me without contacting me" is the consent itself:
the testimonial is born consented, with the survey answer as its proof.
"""
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestCxTestimonialOptin(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.program = cls.env.ref("bf_cx.program_nps_default")
        cls.question = cls.env.ref("bf_cx.question_nps_testimonial")
        cls.direct = cls.env.ref("bf_cx.answer_nps_testimonial_direct")
        cls.contact = cls.env.ref("bf_cx.answer_nps_testimonial_yes")
        cls.no = cls.env.ref("bf_cx.answer_nps_testimonial_no")
        cls.public = cls.env.ref("base.public_user")
        cls.partner = cls.env["res.partner"].create(
            {"name": "Cliente Témoin", "email": "temoin-cx@example.com"}
        )
        cls.env["ir.config_parameter"].sudo().set_param(
            "bf_cx.solicitation_cooldown_days", "0"
        )

    def _answer(self, answer, comment="Excellent accompagnement", score="10"):
        wave = self.env["bf.cx.wave"].create(
            {
                "name": "Vague témoignage",
                "program_id": self.program.id,
                "partner_ids": [(6, 0, self.partner.ids)],
            }
        )
        wave.action_send()
        user_input = wave.user_input_ids
        user_input._save_lines(self.program.score_question_id, score)
        if comment:
            user_input._save_lines(self.program.comment_question_id, comment)
        if answer:
            user_input._save_lines(self.question, answer.id)
        # Submitted the way a client does: from the token link, no session.
        user_input.with_user(self.public).sudo()._mark_done()
        return self.env["bf.cx.feedback"].search(
            [("survey_user_input_id", "=", user_input.id)]
        )

    def _activities(self, feedback):
        return self.env["mail.activity"].search(
            [
                ("res_model", "=", "bf.cx.feedback"),
                ("res_id", "=", feedback.id),
            ]
        )

    def test_default_program_is_wired(self):
        self.assertEqual(self.program.testimonial_direct_answer_id, self.direct)
        self.assertEqual(self.direct.question_id, self.question)
        self.assertLess(self.direct.sequence, self.contact.sequence)

    def test_quote_without_contact_creates_consented_testimonial(self):
        feedback = self._answer(self.direct)
        self.assertTrue(feedback.is_testimonial_candidate)
        self.assertTrue(feedback.testimonial_consent_direct)
        testimonial = feedback.testimonial_id
        self.assertTrue(testimonial, "the consent must produce a testimonial")
        self.assertEqual(testimonial.state, "consented")
        self.assertEqual(testimonial.consent_mode, "survey")
        self.assertEqual(testimonial.body, "Excellent accompagnement")
        self.assertEqual(testimonial.partner_id, self.partner)
        self.assertIn(str(feedback.survey_user_input_id.id), testimonial.consent_note)
        self.assertIn("lien d'invitation personnel", testimonial.consent_note)
        # The wording the respondent saw, in their language.
        self.assertIn(
            self.direct.with_context(lang=self.partner.lang).value,
            testimonial.consent_note,
        )
        testimonial.action_publish()
        self.assertEqual(testimonial.state, "published")
        summaries = self._activities(feedback).mapped("summary")
        self.assertTrue(any("publier" in s for s in summaries), summaries)
        self.assertFalse(any("recontacter" in s for s in summaries), summaries)

    def test_contact_me_only_flags_a_candidate(self):
        feedback = self._answer(self.contact)
        self.assertTrue(feedback.is_testimonial_candidate)
        self.assertFalse(feedback.testimonial_consent_direct)
        self.assertFalse(feedback.testimonial_id)
        activity = self._activities(feedback).filtered(
            lambda a: "recontacter" in a.summary
        )
        self.assertTrue(activity)
        # The note must not overstate the consent.
        self.assertIn("à condition d'être recontacté", activity.note)

    def test_quote_without_contact_but_no_comment_asks_for_contact(self):
        feedback = self._answer(self.direct, comment=False)
        self.assertTrue(feedback.testimonial_consent_direct)
        self.assertFalse(feedback.testimonial_id)
        summaries = self._activities(feedback).mapped("summary")
        self.assertTrue(any("recontacter" in s for s in summaries), summaries)

    def test_no_thanks_leaves_nothing(self):
        feedback = self._answer(self.no)
        self.assertFalse(feedback.is_testimonial_candidate)
        self.assertFalse(feedback.testimonial_consent_direct)
        self.assertFalse(feedback.testimonial_id)

    def test_a_detractor_is_never_quoted_without_a_conversation(self):
        feedback = self._answer(self.direct, score="3")
        self.assertTrue(feedback.testimonial_consent_direct)
        self.assertFalse(feedback.testimonial_id)
        summaries = self._activities(feedback).mapped("summary")
        self.assertTrue(any("recontacter" in s for s in summaries), summaries)

    def test_consent_does_not_depend_on_the_reminder_setting(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_cx.testimonial_activity", "False"
        )
        feedback = self._answer(self.direct)
        self.assertEqual(feedback.testimonial_id.state, "consented")
        self.assertFalse(self._activities(feedback).filtered(
            lambda a: "émoignage" in (a.summary or "")))

    def test_an_answer_filled_from_the_back_office_is_not_a_consent(self):
        """Someone with survey rights fills the client's answer themselves."""
        sondeur = self.env["res.users"].create({
            "name": "Sondeur Interne", "login": "sondeur-interne@essai.invalid",
            "groups_id": [(6, 0, [
                self.env.ref("base.group_user").id,
                self.env.ref("survey.group_survey_user").id,
            ])],
        })
        wave = self.env["bf.cx.wave"].create({
            "name": "Vague contrefaite",
            "program_id": self.program.id,
            "partner_ids": [(6, 0, self.partner.ids)],
        })
        wave.action_send()
        user_input = wave.user_input_ids
        user_input._save_lines(self.program.score_question_id, "10")
        user_input._save_lines(self.program.comment_question_id, "Citez-moi")
        user_input._save_lines(self.question, self.direct.id)
        user_input.with_user(sondeur).sudo()._mark_done()
        feedback = self.env["bf.cx.feedback"].search(
            [("survey_user_input_id", "=", user_input.id)])
        self.assertTrue(feedback.is_testimonial_candidate)
        self.assertFalse(feedback.testimonial_consent_direct)
        self.assertFalse(feedback.testimonial_id)

    def test_an_answer_outside_any_wave_is_not_a_consent(self):
        user_input = self.program.survey_id._create_answer(
            partner=self.partner, check_attempts=False)
        user_input._save_lines(self.program.score_question_id, "10")
        user_input._save_lines(self.program.comment_question_id, "Citez-moi")
        user_input._save_lines(self.question, self.direct.id)
        user_input.with_user(self.public).sudo()._mark_done()
        feedback = self.env["bf.cx.feedback"].search(
            [("survey_user_input_id", "=", user_input.id)])
        self.assertFalse(feedback.testimonial_consent_direct)
        self.assertFalse(feedback.testimonial_id)
