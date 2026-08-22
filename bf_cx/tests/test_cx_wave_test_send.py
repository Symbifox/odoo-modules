"""The test-send button: it must reach the test contact and nothing else.

The expensive failure here is not "the test mail did not leave", it is a
test entry that quietly counts: one that inflates the invitation count,
that drags the wave's NPS toward a number nobody answered, or that lands
in the registry as if a client had spoken. Each of those gets its own
assertion below, because each of them would look perfectly fine on the
wave form.
"""
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

SEND = "odoo.addons.mail.models.mail_mail.MailMail.send"


@tagged("post_install", "-at_install")
class TestCxWaveTestSend(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Param = cls.env["ir.config_parameter"].sudo()
        cls.program = cls.env.ref("bf_cx.program_nps_default")
        cls.client = cls.env["res.partner"].create(
            {"name": "Client Réel", "email": "client-reel@example.com"}
        )
        cls.tester = cls.env["res.partner"].create(
            {"name": "Contact Essai", "email": "essai@example.com"}
        )
        cls.Param.set_param("bf_cx.test_partner_id", str(cls.tester.id))
        cls.Param.set_param("bf_cx.solicitation_cooldown_days", "0")

    def _wave(self):
        return self.env["bf.cx.wave"].create({
            "name": "Vague pour essai",
            "program_id": self.program.id,
            "partner_ids": [(6, 0, self.client.ids)],
        })

    def _test_answers(self, wave):
        return wave.user_input_ids.filtered("test_entry")

    # ── Configuration ────────────────────────────────────────────────────

    def test_unconfigured_test_contact_is_said_not_guessed(self):
        """No test contact set: refuse out loud rather than pick someone."""
        self.Param.set_param("bf_cx.test_partner_id", "")
        wave = self._wave()
        with self.assertRaises(UserError):
            wave.action_send_test()
        self.assertFalse(wave.user_input_ids)

    def test_test_contact_without_email_is_refused(self):
        mute = self.env["res.partner"].create({"name": "Sans courriel"})
        self.Param.set_param("bf_cx.test_partner_id", str(mute.id))
        with self.assertRaises(UserError):
            self._wave().action_send_test()

    # ── L'essai atteint le contact d'essai, et personne d'autre ───────────

    def test_only_the_test_contact_is_reached(self):
        wave = self._wave()
        with patch(SEND):
            wave.action_send_test()
        answers = wave.user_input_ids
        self.assertEqual(len(answers), 1)
        self.assertEqual(answers.partner_id, self.tester)
        self.assertTrue(answers.test_entry)
        mails = self.env["mail.mail"].search(
            [("model", "=", "survey.user_input"), ("res_id", "=", answers.id)]
        )
        self.assertEqual(len(mails), 1, "un courriel, et un seul")
        self.assertEqual(mails.recipient_ids, self.tester)

    def test_test_contact_with_a_portal_account_still_works(self):
        """The realistic case, and the one a clean fixture hides.

        You test with an address of your own, and that address usually has
        a portal user behind it. Odoo's test-token guard checks the
        RECIPIENT's access to the survey rather than the caller's, so
        creating the answer with test_entry=True up front dies on
        "Creating test token is not allowed for you" - a failure that no
        fixture built on a plain partner will ever show.
        """
        portal = self.env.ref("base.group_portal")
        user = self.env["res.users"].create({
            "name": "Contact Essai Portail",
            "login": "essai-portail@example.com",
            "email": "essai-portail@example.com",
            "groups_id": [(6, 0, [portal.id])],
        })
        self.Param.set_param("bf_cx.test_partner_id", str(user.partner_id.id))
        wave = self._wave()
        with patch(SEND):
            wave.action_send_test()
        answer = self._test_answers(wave)
        self.assertEqual(len(answer), 1)
        self.assertEqual(answer.partner_id, user.partner_id)
        self.assertTrue(answer.test_entry, "l'entrée reste bien une entrée de test")

    def test_wave_itself_does_not_move(self):
        wave = self._wave()
        with patch(SEND):
            wave.action_send_test()
        self.assertEqual(wave.state, "draft", "un essai n'envoie pas la vague")
        self.assertFalse(wave.sent_date)
        self.assertEqual(wave.partner_ids, self.client,
                         "le contact d'essai ne rejoint pas les destinataires")

    # ── L'essai ne compte nulle part ──────────────────────────────────────

    def test_test_entry_stays_out_of_the_counters(self):
        wave = self._wave()
        with patch(SEND):
            wave.action_send_test()
        wave.invalidate_recordset()
        self.assertEqual(wave.invited_count, 0)
        self.assertEqual(wave.completed_count, 0)
        self.assertEqual(wave.completion_rate, 0.0)

    def test_answering_a_test_writes_nothing_to_the_registry(self):
        """The whole point: a test answer must not read as a client voice."""
        wave = self._wave()
        with patch(SEND):
            wave.action_send_test()
        answer = self._test_answers(wave)
        answer._save_lines(self.program.score_question_id, "1")
        answer._save_lines(self.program.comment_question_id, "Essai")
        answer._mark_done()
        self.assertFalse(
            self.env["bf.cx.feedback"].search(
                [("survey_user_input_id", "=", answer.id)]
            ),
            "une entrée de test n'entre pas au registre",
        )
        self.assertFalse(
            self.env["bf.cx.feedback"].search([("wave_id", "=", wave.id)]),
            "la vague ne compte aucun feedback après un essai",
        )
        self.assertFalse(
            self.env["mail.activity"].search([
                ("res_model", "=", "bf.cx.feedback"),
                ("create_date", ">=", answer.create_date),
            ]),
            "aucune boucle détracteur sur un essai",
        )
        wave.invalidate_recordset()
        self.assertEqual(wave.nps_score, 0, "un 1/10 d'essai ne tire pas le score")

    def test_test_contact_is_never_stamped_as_solicited(self):
        wave = self._wave()
        with patch(SEND):
            wave.action_send_test()
        self.tester.invalidate_recordset()
        self.assertFalse(self.tester.bf_cx_last_solicited)

    # ── L'essai est rejouable ─────────────────────────────────────────────

    def test_cooldown_does_not_block_a_second_test(self):
        """A guard that blocks the test would make the test useless."""
        self.Param.set_param("bf_cx.solicitation_cooldown_days", "365")
        self.program.cooldown_days = 365
        wave = self._wave()
        with patch(SEND):
            wave.action_send_test()
            wave.action_send_test()
        self.assertEqual(len(self._test_answers(wave)), 2)

    def test_blacklist_does_not_block_the_test(self):
        self.env["mail.blacklist"].sudo()._add(self.tester.email)
        wave = self._wave()
        with patch(SEND):
            wave.action_send_test()
        self.assertEqual(len(self._test_answers(wave)), 1)

    # ── L'essai ne contamine pas les envois réels ─────────────────────────

    def test_reminder_skips_the_test_entry(self):
        """Otherwise the test contact gets chased like a real non-respondent."""
        wave = self._wave()
        with patch(SEND):
            wave.action_send()
            wave.action_send_test()
            before = self.env["mail.mail"].search_count([])
            wave.action_remind()
            created = self.env["mail.mail"].search_count([]) - before
        self.assertEqual(created, 1, "seul le vrai non-répondant est relancé")
        reminded = self.env["mail.mail"].search(
            [("model", "=", "survey.user_input")], order="id desc", limit=1
        )
        self.assertEqual(reminded.recipient_ids, self.client)

    def test_real_send_still_reaches_every_real_recipient(self):
        wave = self._wave()
        with patch(SEND):
            wave.action_send_test()
            wave.action_send()
        self.assertEqual(wave.state, "sent")
        real = wave.user_input_ids.filtered(lambda a: not a.test_entry)
        self.assertEqual(real.partner_id, self.client)
        wave.invalidate_recordset()
        self.assertEqual(wave.invited_count, 1)
