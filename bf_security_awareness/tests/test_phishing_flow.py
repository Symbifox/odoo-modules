from datetime import timedelta
from urllib.parse import quote

from odoo import fields
from odoo.tests import HttpCase, TransactionCase, tagged


class PhishingFlowCommon(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.channel = cls.env["slide.channel"].create({
            "name": "Test course",
            "channel_type": "training",
            "enroll": "invite",
            "visibility": "connected",
        })
        cls.template = cls.env["bf.phishing.template"].create({
            "name": "Test lure",
            "subject": "Action requise",
            "difficulty": "easy",
            "landing_mode": "credential",
            "capture_field_lengths": True,
            "training_channel_id": cls.channel.id,
        })
        cls.partner = cls.env["res.partner"].create({
            "name": "Alice Target",
            "email": "alice.target@example.com",
        })
        cls.campaign = cls.env["bf.phishing.campaign"].create({
            "name": "Q2 test",
            "template_id": cls.template.id,
            "recipient_partner_ids": [(6, 0, cls.partner.ids)],
        })


@tagged("post_install", "-at_install")
class TestPhishingFlow(PhishingFlowCommon):

    def test_prepare_materialises_results(self):
        self.campaign.action_prepare()
        self.assertEqual(self.campaign.state, "scheduled")
        results = self.campaign.result_ids
        self.assertEqual(len(results), 1)
        res = results[0]
        self.assertTrue(res.token and len(res.token) >= 32)
        self.assertEqual(res.email, "alice.target@example.com")
        self.assertTrue(self.campaign.utm_campaign_id)
        self.assertTrue(self.campaign.mailing_id)
        self.assertEqual(
            self.campaign.mailing_id.mailing_model_real, "bf.phishing.result")
        # A risk profile is auto-created for the targeted person.
        self.assertTrue(res.profile_id)

    def test_prepare_is_idempotent(self):
        self.campaign.action_prepare()
        self.campaign.action_reset_to_draft()
        self.campaign.action_prepare()
        self.assertEqual(len(self.campaign.result_ids), 1)

    def test_state_ratchets_forward_only(self):
        self.campaign.action_prepare()
        res = self.campaign.result_ids[0]
        res.register_sent()
        self.assertEqual(res.state, "sent")
        res.register_open()
        self.assertEqual(res.state, "opened")
        res.register_click(ip="203.0.113.5", user_agent="UA")
        self.assertEqual(res.state, "clicked")
        self.assertEqual(res.click_ip, "203.0.113.5")
        # Re-registering an open must NOT move the state backwards.
        res.register_open()
        self.assertEqual(res.state, "clicked")
        res.register_submit(username_len=4, password_len=9)
        self.assertEqual(res.state, "submitted")
        # A late click stays at the worst observed outcome.
        res.register_click(ip="203.0.113.9")
        self.assertEqual(res.state, "submitted")
        self.assertEqual(res.click_ip, "203.0.113.5")  # first IP kept

    def test_click_auto_assigns_training(self):
        self.campaign.action_prepare()
        res = self.campaign.result_ids[0]
        res.register_click()
        self.assertTrue(res.training_assignment_id)
        assignment = res.training_assignment_id
        self.assertEqual(assignment.partner_id, self.partner)
        self.assertEqual(assignment.channel_id, self.channel)
        # A second failing result for the same person reuses the open assignment.
        partner2_camp = self.env["bf.phishing.campaign"].create({
            "name": "Q2 test 2",
            "template_id": self.template.id,
            "recipient_partner_ids": [(6, 0, self.partner.ids)],
        })
        partner2_camp.action_prepare()
        res2 = partner2_camp.result_ids[0]
        res2.register_submit()
        self.assertEqual(
            res2.training_assignment_id, assignment,
            "should reuse the existing open training assignment")

    def test_refail_after_completed_links_existing(self):
        # Re-failing after a COMPLETED assignment for the same (partner, channel)
        # must link the existing row, not raise the unique-constraint error.
        self.campaign.action_prepare()
        res1 = self.campaign.result_ids[0]
        res1.register_click()
        done = res1.training_assignment_id
        done.state = "completed"

        camp2 = self.env["bf.phishing.campaign"].create({
            "name": "Q2 retest",
            "template_id": self.template.id,
            "recipient_partner_ids": [(6, 0, self.partner.ids)],
        })
        camp2.action_prepare()
        res2 = camp2.result_ids[0]
        res2.register_submit()  # should not raise
        self.assertEqual(res2.training_assignment_id, done)

    def test_send_window_drip_release(self):
        self.campaign.send_window_days = 5.0
        self.campaign.action_prepare()
        self.campaign.action_launch()
        self.assertEqual(self.campaign.state, "running")
        res = self.campaign.result_ids[0]
        self.assertTrue(res.scheduled_send)
        # Clearly future → not released yet.
        res.write({
            "state": "pending",
            "scheduled_send": fields.Datetime.now() + timedelta(days=3),
            "sent_datetime": False,
        })
        self.campaign._release_due_results()
        self.assertEqual(res.state, "pending")
        # Now due → released (state advances even if SMTP is unavailable).
        res.scheduled_send = fields.Datetime.now() - timedelta(minutes=1)
        self.campaign._release_due_results()
        self.assertEqual(res.state, "sent")

    def test_qr_src_encodes_landing(self):
        self.campaign.action_prepare()
        res = self.campaign.result_ids[0]
        self.assertIn("/report/barcode/", res.qr_src)
        self.assertIn("barcode_type=QR", res.qr_src)
        self.assertIn(quote(res.landing_url, safe=""), res.qr_src)

    def test_report_is_positive(self):
        self.campaign.action_prepare()
        res = self.campaign.result_ids[0]
        res.register_report()
        self.assertTrue(res.reported)
        self.assertTrue(res.reported_datetime)

    def test_audience_from_mailing_list(self):
        # Targeting via a mailing.list must resolve contacts to partners
        # (find-or-create by email). mailing.contact has no partner_id, so a
        # naive mapped('partner_id') would raise — guard against regressing.
        mlist = self.env["mailing.list"].create({"name": "SA test list"})
        # Contact whose email already matches an existing partner -> reuse it.
        self.env["mailing.contact"].create({
            "name": "Alice", "email": self.partner.email,
            "list_ids": [(6, 0, mlist.ids)]})
        # Contact with a brand-new email -> a partner is materialised.
        self.env["mailing.contact"].create({
            "name": "Newbie", "email": "newbie@example.com",
            "list_ids": [(6, 0, mlist.ids)]})
        camp = self.env["bf.phishing.campaign"].create({
            "name": "List camp",
            "template_id": self.template.id,
            "mailing_list_ids": [(6, 0, mlist.ids)],
        })
        camp.action_prepare()  # must not raise
        self.assertEqual(camp.state, "scheduled")
        emails = set(camp.result_ids.mapped("email"))
        self.assertIn(self.partner.email, emails)
        self.assertIn("newbie@example.com", emails)
        # The existing partner was reused, the new email created exactly one.
        self.assertEqual(self.env["res.partner"].search_count(
            [("email", "=", "newbie@example.com")]), 1)


@tagged("post_install", "-at_install")
class TestPhishingController(HttpCase):

    def setUp(self):
        super().setUp()
        self.template = self.env["bf.phishing.template"].create({
            "name": "HTTP lure",
            "subject": "Hi",
            "landing_mode": "credential",
            "capture_field_lengths": True,
        })
        self.partner = self.env["res.partner"].create({
            "name": "Bob", "email": "bob@example.com"})
        self.campaign = self.env["bf.phishing.campaign"].create({
            "name": "HTTP camp",
            "template_id": self.template.id,
            "recipient_partner_ids": [(6, 0, self.partner.ids)],
        })
        self.campaign.action_prepare()
        self.result = self.campaign.result_ids[0]

    def test_landing_registers_click(self):
        resp = self.url_open("/phish/%s" % self.result.token)
        self.assertEqual(resp.status_code, 200)
        self.result.invalidate_recordset()
        self.assertEqual(self.result.state, "clicked")

    def test_open_pixel(self):
        resp = self.url_open("/phish/%s/open.png" % self.result.token)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers.get("Content-Type"), "image/png")
        self.result.invalidate_recordset()
        self.assertTrue(self.result.opened_datetime)

    def test_invalid_token_is_constant(self):
        # Unknown token must behave identically to the teachable page: 200, no
        # information leak, no 404.
        resp = self.url_open("/phish/this-token-does-not-exist")
        self.assertEqual(resp.status_code, 200)
        resp_pixel = self.url_open("/phish/nope/open.png")
        self.assertEqual(resp_pixel.status_code, 200)
        self.assertEqual(resp_pixel.headers.get("Content-Type"), "image/png")
