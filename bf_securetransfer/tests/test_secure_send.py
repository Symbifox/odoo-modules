"""Secure send (Idea #1): hold the mail until the recipient proves identity by
a one-time code, delivered by e-mail or SMS.

Covers the model gate (force_recipient_otp / channel selection / fallback), the
SMS delivery seam (VoIP.ms mocked — the suite never touches the network) and
the backend wizard (creates an active, OTP-gated message whose notification
never carries the body).
"""
from unittest.mock import patch

from odoo.tests import TransactionCase, tagged

from odoo.addons.bf_securetransfer.models import sms as sms_mod

from .common import LanguesActives

SMS_MOD = "odoo.addons.bf_securetransfer.models.sms"


@tagged("post_install", "-at_install")
class TestSecureSend(LanguesActives, TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.brand = cls.env.ref("bf_securetransfer.brand_default")
        # The wizard guards on the securetransfer user group. The MANAGER group
        # is needed too: since 18.0.1.18.0 only a manager may compose under an
        # identity other than his own, and these tests do exactly that.
        cls.env.user.groups_id = [
            (4, cls.env.ref("bf_securetransfer.group_securetransfer_user").id),
            (4, cls.env.ref("bf_securetransfer.group_securetransfer_manager").id)]
        icp = cls.env["ir.config_parameter"].sudo()
        icp.set_param("bf_securetransfer.quota_daily_transfers_per_sender", "500")
        icp.set_param("bf_securetransfer.quota_daily_transfers_per_ip", "500")

    _n = 0

    def _addr(self):
        """A unique recipient address per call (so a stray res.partner match in
        the DB can't leak a phone into _phone_for_recipient)."""
        type(self)._n += 1
        return "dest-st-%d@example.com" % self._n

    def _draft(self, **ov):
        vals = {
            "brand_id": self.brand.id,
            "sender_name": "S", "sender_email": "s@example.com",
            "recipient_emails": ov.pop("recipient_emails", None) or self._addr(),
            "message": "secret-body-XYZ",
            "retention_days": 7, "state": "draft",
        }
        vals.update(ov)
        return self.env["secure.transfer"].create(vals)

    # ------------------------------------------------------------------ normalize
    def test_normalize_na(self):
        self.assertEqual(sms_mod.normalize_na("+1 (514) 555-1234"), "5145551234")
        self.assertEqual(sms_mod.normalize_na("1-514-555-1234"), "5145551234")
        self.assertEqual(sms_mod.normalize_na("5145551234"), "5145551234")
        self.assertIsNone(sms_mod.normalize_na("12345"))
        self.assertIsNone(sms_mod.normalize_na("+44 20 7946 0000"))
        self.assertIsNone(sms_mod.normalize_na(""))

    # ------------------------------------------------------------------ gate
    def test_force_flag_requires_otp(self):
        self.assertTrue(self._draft(force_recipient_otp=True)._recipient_otp_required())
        # No force + tenant setting off (default) → no gate.
        self.assertFalse(self._draft()._recipient_otp_required())

    def test_tenant_setting_requires_otp(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_securetransfer.require_recipient_otp", "1")
        try:
            self.assertTrue(self._draft()._recipient_otp_required())
        finally:
            self.env["ir.config_parameter"].sudo().set_param(
                "bf_securetransfer.require_recipient_otp", "0")

    # ------------------------------------------------------------------ channel
    def test_channel_email_by_default(self):
        addr = self._addr()
        t = self._draft(force_recipient_otp=True, recipient_emails=addr)
        self.assertEqual(t._otp_channel_for(addr), "email")

    def test_channel_sms_needs_config_and_phone(self):
        addr = self._addr()
        t = self._draft(force_recipient_otp=True, recipient_otp_channel="sms",
                        recipient_emails=addr,
                        recipient_sms_map='{"%s": "5145551234"}' % addr)
        # SMS not configured → email.
        with patch(SMS_MOD + ".configured", return_value=False):
            self.assertEqual(t._otp_channel_for(addr), "email")
        # Configured + phone → sms.
        with patch(SMS_MOD + ".configured", return_value=True):
            self.assertEqual(t._otp_channel_for(addr), "sms")
        # Configured but no phone known → email (never a lock-out).
        addr2 = self._addr()
        t2 = self._draft(force_recipient_otp=True, recipient_otp_channel="sms",
                         recipient_emails=addr2)
        with patch(SMS_MOD + ".configured", return_value=True):
            self.assertEqual(t2._otp_channel_for(addr2), "email")

    # ------------------------------------------------------------------ delivery
    def test_send_recipient_otp_uses_sms(self):
        addr = self._addr()
        t = self._draft(force_recipient_otp=True, recipient_otp_channel="sms",
                        recipient_emails=addr,
                        recipient_sms_map='{"%s": "5145551234"}' % addr)
        calls = []

        def fake_send(env, dst, message):
            calls.append((dst, message))
            return True

        with patch(SMS_MOD + ".configured", return_value=True), \
                patch(SMS_MOD + ".send", side_effect=fake_send):
            otp_hash, expiry = t._send_recipient_otp(addr)
        self.assertTrue(otp_hash)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "5145551234")
        self.assertRegex(calls[0][1], r"\d{6}")
        self.assertTrue(any("SMS" in (n or "")
                            for n in t.access_log_ids.mapped("note")))

    def test_send_recipient_otp_falls_back_to_email(self):
        addr = self._addr()
        t = self._draft(force_recipient_otp=True, recipient_otp_channel="sms",
                        recipient_emails=addr,
                        recipient_sms_map='{"%s": "5145551234"}' % addr)
        with patch(SMS_MOD + ".configured", return_value=True), \
                patch(SMS_MOD + ".send", return_value=False):
            otp_hash, expiry = t._send_recipient_otp(addr)
        self.assertTrue(otp_hash)
        self.assertTrue(any("courriel" in (n or "")
                            for n in t.access_log_ids.mapped("note")))

    def test_send_recipient_otp_rejects_stranger(self):
        t = self._draft(force_recipient_otp=True)
        self.assertEqual(t._send_recipient_otp("nobody@example.com"), (None, None))

    # ------------------------------------------------------------------ wizard
    def test_wizard_default_brand(self):
        defaults = self.env["secure.transfer.send.wizard"].default_get(["brand_id"])
        self.assertEqual(defaults.get("brand_id"), self.brand.id)

    def test_wizard_creates_gated_message_without_leaking_body(self):
        partner = self.env["res.partner"].create({
            "name": "Wiz Dest", "email": "wiz-dest@example.com",
            "mobile": "514 555 9876"})
        wiz = self.env["secure.transfer.send.wizard"].create({
            "brand_id": self.brand.id,
            "partner_ids": [(6, 0, partner.ids)],
            "message": "TOP-SECRET-123",
            "otp_channel": "email",
            "sender_email": "me@example.com", "sender_name": "Me",
        })
        res = wiz.action_send()
        transfer = self.env["secure.transfer"].browse(res["res_id"])
        self.assertEqual(transfer.state, "active")
        self.assertTrue(transfer.force_recipient_otp)
        self.assertTrue(transfer._recipient_otp_required())
        self.assertIn("wiz-dest@example.com", transfer.recipient_emails)
        self.assertEqual(transfer.message, "TOP-SECRET-123")
        # The mobile is captured for the sms map even when channel is email.
        self.assertIn("5145559876", transfer.recipient_sms_map or "")
        # The recipient notification must carry the LINK ONLY — never the body.
        mails = self.env["mail.mail"].search([
            ("model", "=", "secure.transfer"), ("res_id", "=", transfer.id)])
        rec_mail = mails.filtered(lambda m: "wiz-dest@example.com" in (m.email_to or ""))
        self.assertTrue(rec_mail, "recipient notification not queued")
        for m in rec_mail:
            self.assertNotIn("TOP-SECRET-123", m.body_html or "")
            # Link-only notification: the share link is present (proof it is the
            # secure-message template) — asserted language-independently, since
            # the body renders in the recipient's language (fr_CA or en_CA).
            self.assertIn("/s/", m.body_html or "")

    def test_wizard_sms_channel_requires_configuration(self):
        partner = self.env["res.partner"].create({
            "name": "Wiz Dest2", "email": "wiz-dest2@example.com",
            "mobile": "514 555 0000"})
        wiz = self.env["secure.transfer.send.wizard"].create({
            "brand_id": self.brand.id,
            "partner_ids": [(6, 0, partner.ids)],
            "message": "hello", "otp_channel": "sms",
            "sender_email": "me@example.com",
        })
        from odoo.exceptions import UserError
        with patch(SMS_MOD + ".configured", return_value=False):
            with self.assertRaises(UserError):
                wiz.action_send()
