# -*- coding: utf-8 -*-
"""Device tokens after the 2026-09-08 audit (S-M2, S-M3, S-M5). Same rules as
the SMS half; each test carries the value that would pass the old code."""
from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.bf_email_management.models.push_transport import ntfy_auth_allowed


@tagged("post_install", "-at_install")
class TestMobileTokens(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = new_test_user(cls.env, login="mail_tok_user", groups="base.group_user")
        cls.other = new_test_user(cls.env, login="mail_tok_other", groups="base.group_user")
        cls.Device = cls.env["bf.email.mobile.device"]

    def _issue(self, user=None):
        device = self.Device._issue((user or self.user).id, name="Banc jetons")
        return device, device.device_token

    def test_recognised_by_hash_then_sealed(self):
        device, raw = self._issue()
        self.assertEqual(device.token_hash, self.Device._hash_token(raw))
        self.assertEqual(self.Device._resolve(raw), device)
        self.assertFalse(self.Device._resolve(device.token_hash))
        device._seal()
        self.assertFalse(device.device_token)
        self.assertEqual(self.Device._resolve(raw), device)

    def test_legacy_row_migrates_on_first_use(self):
        legacy = self.Device.sudo().create({
            "user_id": self.user.id, "name": "Ancien",
            "device_token": "clear-token-from-before", "token_hash": False})
        self.assertEqual(self.Device._resolve("clear-token-from-before"), legacy)
        self.assertEqual(legacy.token_hash, self.Device._hash_token("clear-token-from-before"))
        self.assertFalse(legacy.device_token)

    def test_idle_device_is_revoked(self):
        device, raw = self._issue()
        device.sudo().write({"last_seen": fields.Datetime.now() - timedelta(days=91)})
        self.assertFalse(self.Device._resolve(raw))
        self.assertFalse(device.active)

    def test_no_one_mints_a_token_by_hand(self):
        device, _raw = self._issue()
        Other = self.Device.with_user(self.user)
        with self.assertRaises(AccessError):
            Other.browse(device.id).write({"token_hash": self.Device._hash_token("chosen")})
        with self.assertRaises(AccessError):
            Other.browse(device.id).write({"user_id": self.other.id})
        Other.browse(device.id).write({"name": "Renamed"})

    def test_ntfy_token_only_goes_to_our_host(self):
        base = "https://ntfy.example.test"
        self.assertTrue(ntfy_auth_allowed("https://ntfy.example.test/upAbc", base))
        self.assertFalse(ntfy_auth_allowed("https://attacker.example/upAbc", base))
        icp = self.env["ir.config_parameter"].sudo()
        icp.set_param("bf_email_management.ntfy_publish_token", "tk-bench")
        icp.set_param("bf_sms_archive.ntfy_base_url", base)
        Push = self.env["bf.email.unifiedpush"]
        self.assertIn("Authorization", Push._auth_headers("https://ntfy.example.test/upAbc"))
        self.assertNotIn("Authorization", Push._auth_headers("https://attacker.example/upAbc"))
