"""End-to-end tests for the CX core flows.

Mirrors the staging smoke test: survey ingestion + closed loop, rating
ingestion + answer correction, solicitation cooldown, complaint sequence,
testimonial proof lock, NPS scale constraint.
"""
from odoo.exceptions import UserError, ValidationError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestCxFlow(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.program = cls.env.ref("bf_cx.program_nps_default")
        cls.partner = cls.env["res.partner"].create(
            {"name": "Client Test CX", "email": "client-cx@example.com"}
        )
        cls.env["ir.config_parameter"].sudo().set_param(
            "bf_cx.solicitation_cooldown_days", "0"
        )

    def _make_wave(self, partners):
        return self.env["bf.cx.wave"].create(
            {
                "name": "Vague test",
                "program_id": self.program.id,
                "partner_ids": [(6, 0, partners.ids)],
            }
        )

    def _answer(self, user_input, score="2", comment="Trop lent"):
        user_input._save_lines(self.program.score_question_id, score)
        if comment:
            user_input._save_lines(self.program.comment_question_id, comment)
        user_input._mark_done()

    def test_wave_send_and_ingestion(self):
        wave = self._make_wave(self.partner)
        wave.action_send()
        self.assertEqual(wave.state, "sent")
        self.assertEqual(len(wave.user_input_ids), 1)
        user_input = wave.user_input_ids
        self._answer(user_input, score="2")
        feedback = self.env["bf.cx.feedback"].search(
            [("survey_user_input_id", "=", user_input.id)]
        )
        self.assertEqual(len(feedback), 1)
        self.assertEqual(feedback.kind, "nps")
        self.assertEqual(feedback.nps_bucket, "detractor")
        self.assertIn("Trop lent", feedback.comment)
        activity = self.env["mail.activity"].search(
            [
                ("res_model", "=", "bf.cx.feedback"),
                ("res_id", "=", feedback.id),
            ]
        )
        self.assertTrue(activity, "closed loop must schedule an activity")
        # Idempotence: a second _mark_done must not duplicate.
        user_input._mark_done()
        self.assertEqual(
            self.env["bf.cx.feedback"].search_count(
                [("survey_user_input_id", "=", user_input.id)]
            ),
            1,
        )
        self.assertEqual(self.program.nps_score, -100)

    def test_cooldown_blocks_second_wave(self):
        # Ce test porte sur le garde-fou GLOBAL. Le programme de référence
        # peut porter sa propre cadence selon l'installation (90 j sur une
        # base réelle), et elle l'emporte : sans cette remise à zéro, le test
        # mesure la cadence du programme et le dernier envoi reste bloqué.
        self.program.cooldown_days = 0
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_cx.solicitation_cooldown_days", "30"
        )
        wave1 = self._make_wave(self.partner)
        wave1.action_send()
        self.assertTrue(self.partner.bf_cx_last_solicited)
        wave2 = self._make_wave(self.partner)
        with self.assertRaises(UserError):
            wave2.action_send()
        # Cooldown disabled → allowed again.
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_cx.solicitation_cooldown_days", "0"
        )
        wave2.action_send()
        self.assertEqual(wave2.state, "sent")

    def test_blocked_wave_names_the_cadence_that_actually_blocked(self):
        """Un message qui nomme le mauvais réglage coûte une heure à qui débogue.

        Le garde-fou applique `program.cooldown_days or None` : quand le
        programme porte sa cadence, le paramètre global n'est jamais consulté.
        Le message doit donc annoncer la cadence du programme, sinon on part
        corriger un réglage qui n'y est pour rien.
        """
        self.program.cooldown_days = 90
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_cx.solicitation_cooldown_days", "30"
        )
        self._make_wave(self.partner).action_send()
        with self.assertRaises(UserError) as err:
            self._make_wave(self.partner).action_send()
        message = str(err.exception)
        self.assertIn("90", message, "la cadence appliquée doit être nommée")
        self.assertNotIn("30", message, "le paramètre global n'a pas bloqué")
        self.assertIn(self.program.name, message)

    def test_rating_ingestion_and_correction(self):
        task = self.env["project.task"].create(
            {"name": "Tâche test CX"}
        )
        rating = self.env["rating.rating"].create(
            {
                "res_model_id": self.env["ir.model"]._get_id("project.task"),
                "res_id": task.id,
                "partner_id": self.partner.id,
                "rating": 5,
                "consumed": True,
            }
        )
        feedback = rating.bf_cx_feedback_id
        self.assertTrue(feedback)
        self.assertEqual(feedback.kind, "csat")
        self.assertEqual(feedback.score, 5.0)
        # The core reuses the same rating.rating when the client corrects
        # their answer: the mirror must follow and trigger the closed loop.
        rating.write({"rating": 1, "feedback": "Finalement non"})
        self.assertEqual(feedback.score, 1.0)
        self.assertIn("Finalement", feedback.comment)
        activity = self.env["mail.activity"].search(
            [
                ("res_model", "=", "bf.cx.feedback"),
                ("res_id", "=", feedback.id),
            ]
        )
        self.assertTrue(activity)

    def test_complaint_sequence_and_resolution_lock(self):
        complaint = self.env["bf.cx.complaint"].create(
            {
                "name": "Retard",
                "partner_id": self.partner.id,
                "description": "<p>Test</p>",
            }
        )
        self.assertTrue(complaint.number.startswith("PLT"))
        self.assertTrue(complaint.ack_deadline)
        complaint.action_acknowledge()
        self.assertTrue(
            complaint.date_acknowledged,
            "acknowledging must stamp the real AR date (ISO 10002 KPI)",
        )
        complaint.action_start_analysis()
        with self.assertRaises(UserError):
            complaint.action_resolve()
        complaint.resolution = "<p>Réglé</p>"
        complaint.action_resolve()
        self.assertEqual(complaint.state, "resolved")

    def test_testimonial_requires_proof(self):
        testimonial = self.env["bf.cx.testimonial"].create(
            {
                "name": "Témoignage",
                "partner_id": self.partner.id,
                "body": "Excellent service.",
                "consent_mode": "verbal",
            }
        )
        with self.assertRaises(UserError):
            testimonial.action_set_consented()
        testimonial.consent_note = "Appel du 2026-07-23"
        testimonial.action_set_consented()
        testimonial.action_publish()
        self.assertEqual(testimonial.state, "published")
        with self.assertRaises(UserError):
            self.env["bf.cx.testimonial"].create(
                {
                    "name": "T2",
                    "partner_id": self.partner.id,
                    "body": "X",
                }
            ).action_publish()

    def test_needs_followup_covers_csat(self):
        feedback = self.env["bf.cx.feedback"].create(
            {
                "partner_id": self.partner.id,
                "kind": "csat",
                "score": 1,
                "score_max": 5,
                "source": "manual",
            }
        )
        self.assertTrue(
            feedback.needs_followup,
            "a dissatisfied CSAT must surface as 'à rappeler' too",
        )
        feedback.action_mark_done()
        self.assertFalse(
            feedback.activity_ids.filtered(
                lambda a: a.activity_type_id
                == self.env.ref("mail.mail_activity_data_todo")
            ),
            "closing the record must close its automatic to-dos",
        )

    def test_central_rating_guard(self):
        """Core rating requests (project & co) obey the solicitation guard."""
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_cx.solicitation_cooldown_days", "30"
        )
        template = self.env.ref("project.rating_project_request_email_template")
        task = self.env["project.task"].create(
            {"name": "Tâche garde-fou", "partner_id": self.partner.id}
        )
        Rating = self.env["rating.rating"]
        self.partner._bf_cx_mark_solicited()
        before = Rating.search_count(
            [("res_model", "=", "project.task"), ("res_id", "=", task.id)]
        )
        task.rating_send_request(template, force_send=False)
        self.assertEqual(
            Rating.search_count(
                [("res_model", "=", "project.task"), ("res_id", "=", task.id)]
            ),
            before,
            "a recently-solicited partner must not receive a rating request",
        )
        self.partner.bf_cx_last_solicited = False
        task.rating_send_request(template, force_send=False)
        self.assertEqual(
            Rating.search_count(
                [("res_model", "=", "project.task"), ("res_id", "=", task.id)]
            ),
            before + 1,
            "a clean partner must receive the rating request",
        )
        self.assertTrue(self.partner.bf_cx_last_solicited)

    def test_nps_summary_honesty(self):
        Feedback = self.env["bf.cx.feedback"]
        for score in (10, 9, 2):
            Feedback.create(
                {
                    "partner_id": self.partner.id,
                    "kind": "nps",
                    "score": score,
                    "score_max": 10,
                    "source": "manual",
                    "program_id": self.program.id,
                }
            )
        summary = Feedback._nps_summary(
            [("program_id", "=", self.program.id)]
        )
        self.assertEqual(summary["n"], 3)
        self.assertIsNone(
            summary["score"], "under 10 answers the score must be hidden"
        )

    def test_nps_scale_constraint(self):
        with self.assertRaises(ValidationError):
            self.env["bf.cx.feedback"].create(
                {
                    "partner_id": self.partner.id,
                    "kind": "nps",
                    "score": 4,
                    "score_max": 5,
                }
            )
