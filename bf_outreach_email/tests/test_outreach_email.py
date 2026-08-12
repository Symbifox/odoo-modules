# -*- coding: utf-8 -*-
from datetime import timedelta

from odoo import fields
from odoo.tests.common import TransactionCase, tagged

PARAM_WATERMARK = "bf_outreach_email.last_scan"


@tagged("post_install", "-at_install", "bf_outreach")
class TestOutreachEmailBridge(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.now = fields.Datetime.now()
        cls.campaign = cls.env["bf.outreach.campaign"].create(
            {
                "name": "Campagne passerelle courriel",
                "date_start": cls.now.date() - timedelta(days=10),
                "state": "running",
                "stop_on_reply": True,
                "working_days_only": False,
            }
        )
        cls.target = cls.env["bf.outreach.target"].create(
            {
                "name": "Répondante inc.",
                "campaign_id": cls.campaign.id,
                "email": "Repondante@Example.com",
            }
        )
        cls.env["ir.config_parameter"].sudo().set_param(
            PARAM_WATERMARK, fields.Datetime.to_string(cls.now - timedelta(days=1))
        )

    def _inbound(self, sender, subject="Re: votre courriel", when=None):
        return self.env["bf.email"].create(
            {
                "direction": "in",
                "email_from": sender,
                "subject": subject,
                "date": when or self.now,
                "user_id": self.env.user.id,
            }
        )

    def test_inbound_email_becomes_a_reply(self):
        self.assertFalse(self.target.has_reply)
        mail = self._inbound("Répondante <repondante@example.com>")
        created = self.env["bf.outreach.target"]._cron_match_inbound_emails()
        self.assertEqual(created, 1)
        self.assertTrue(self.target.has_reply)
        touch = self.target.touch_ids
        self.assertEqual(touch.kind, "email")
        self.assertEqual(touch.direction, "in")
        self.assertEqual(touch.outcome, "replied")
        self.assertEqual(touch.bf_email_id, mail)
        # « Arrêter à la première réponse » agit tout seul.
        self.assertFalse(self.target.next_action_date)

    def test_same_email_is_never_matched_twice(self):
        self._inbound("repondante@example.com")
        self.env["bf.outreach.target"]._cron_match_inbound_emails()
        # Le filigrane a avancé ; on le recule pour forcer un second passage.
        self.env["ir.config_parameter"].sudo().set_param(
            PARAM_WATERMARK, fields.Datetime.to_string(self.now - timedelta(days=1))
        )
        again = self.env["bf.outreach.target"]._cron_match_inbound_emails()
        self.assertEqual(again, 0)
        self.assertEqual(self.target.touch_count, 1)

    def test_unknown_sender_is_ignored(self):
        self._inbound("inconnu@example.com")
        created = self.env["bf.outreach.target"]._cron_match_inbound_emails()
        self.assertEqual(created, 0)
        self.assertEqual(self.target.touch_count, 0)

    def test_closed_target_is_not_reopened(self):
        self.target.stage_id = self.env.ref("bf_outreach.stage_lost")
        self._inbound("repondante@example.com")
        created = self.env["bf.outreach.target"]._cron_match_inbound_emails()
        self.assertEqual(created, 0)

    def test_watermark_bounds_the_scan(self):
        # Un courriel antérieur au filigrane n'est pas rejoué.
        self._inbound("repondante@example.com", when=self.now - timedelta(days=30))
        created = self.env["bf.outreach.target"]._cron_match_inbound_emails()
        self.assertEqual(created, 0)
        param = self.env["ir.config_parameter"].sudo().get_param(PARAM_WATERMARK)
        self.assertTrue(param)
