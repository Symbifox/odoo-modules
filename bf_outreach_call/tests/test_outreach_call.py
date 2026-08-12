# -*- coding: utf-8 -*-
from datetime import timedelta

from odoo import fields
from odoo.tests.common import TransactionCase, tagged

PARAM_WATERMARK = "bf_outreach_call.last_scan"


@tagged("post_install", "-at_install", "bf_outreach")
class TestOutreachCallBridge(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.now = fields.Datetime.now()
        cls.campaign = cls.env["bf.outreach.campaign"].create(
            {
                "name": "Campagne passerelle appels",
                "date_start": cls.now.date() - timedelta(days=10),
                "state": "running",
                "call_target_count": 3,
                "call_interval_days": 7,
                "stop_on_reply": False,
                "working_days_only": False,
            }
        )
        cls.target = cls.env["bf.outreach.target"].create(
            {
                "name": "Joignable inc.",
                "campaign_id": cls.campaign.id,
                "phone": "514-555-0142",
            }
        )
        cls.env["ir.config_parameter"].sudo().set_param(
            PARAM_WATERMARK, fields.Datetime.to_string(cls.now - timedelta(days=1))
        )

    def _ingest(self, call_type="outgoing", duration=0, minutes_ago=5, number="5145550142"):
        when = self.now - timedelta(minutes=minutes_ago)
        record, _created = self.env["call.archive.call"]._ingest_one(
            phone_raw=number,
            owner_id=self.env.uid,
            call_type=call_type,
            date_ms=int(when.timestamp() * 1000),
            duration=duration,
            batch_id="test",
        )
        return record

    def _run(self):
        return self.env["bf.outreach.target"]._cron_match_archived_calls()

    def test_target_phone_is_normalised_like_the_archive(self):
        self.assertEqual(self.target.phone_normalized, "+15145550142")

    def test_answered_outgoing_call_becomes_a_reached_touch(self):
        call = self._ingest("outgoing", duration=185)
        self.assertEqual(self._run(), 1)
        touch = self.target.touch_ids
        self.assertEqual(touch.kind, "call")
        self.assertEqual(touch.direction, "out")
        self.assertEqual(touch.outcome, "reached")
        self.assertAlmostEqual(touch.duration, 3.08, places=2)
        self.assertEqual(touch.call_archive_id, call)
        # La cadence a avancé sans saisie manuelle.
        self.assertEqual(self.target.call_count, 1)

    def test_unanswered_call_is_a_no_answer(self):
        self._ingest("outgoing", duration=0)
        self._run()
        self.assertEqual(self.target.touch_ids.outcome, "no_answer")

    def test_incoming_call_is_recorded_as_incoming(self):
        self._ingest("incoming", duration=42)
        self._run()
        touch = self.target.touch_ids
        self.assertEqual(touch.direction, "in")
        self.assertTrue(touch.is_reply)

    def test_rejected_calls_are_ignored(self):
        self._ingest("rejected", duration=0)
        self.assertEqual(self._run(), 0)
        self.assertEqual(self.target.touch_count, 0)

    def test_same_call_is_never_matched_twice(self):
        self._ingest("outgoing", duration=60)
        self._run()
        self.env["ir.config_parameter"].sudo().set_param(
            PARAM_WATERMARK, fields.Datetime.to_string(self.now - timedelta(days=1))
        )
        self.assertEqual(self._run(), 0)
        self.assertEqual(self.target.touch_count, 1)

    def test_unknown_number_is_ignored(self):
        self._ingest("outgoing", duration=30, number="5145559999")
        self.assertEqual(self._run(), 0)
