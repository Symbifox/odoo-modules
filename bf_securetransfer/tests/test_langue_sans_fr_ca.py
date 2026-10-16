"""A database without fr_CA: the public pages still answer.

The product writes fr_CA as its default locale, and the pages used to push it
into the request context as is. ``Environment.lang`` (odoo/api.py) raises
« Invalid language code » for a language that is not active, so on any database
without fr_CA — a public install outside Quebec, or the CI's fresh database —
every public page answered 400 before rendering a single line.

Unlike the rest of the suite, this class does NOT use ``BaseNeuve``: activating
fr_CA is exactly what would hide the defect.
"""
from unittest.mock import patch

from odoo.tests import HttpCase, tagged

from odoo.addons.bf_securetransfer.controllers import main as st_main
from odoo.addons.bf_securetransfer.models.secure_transfer import active_lang

S3_MOD = "odoo.addons.bf_securetransfer.models.s3"


@tagged("post_install", "-at_install")
class TestLangueSansFrCa(HttpCase):

    def setUp(self):
        super().setUp()
        actives = [code for code, _name in self.env["res.lang"].get_installed()]
        if "fr_CA" in actives:
            self.skipTest("fr_CA is active here: the old fallback landed on it "
                          "and passed, so this database cannot show the defect")
        for limiter in (st_main._token_fail_limiter, st_main._create_limiter):
            with limiter._lock:
                limiter._data.clear()
        self.brand = self.env.ref("bf_securetransfer.brand_default")
        self.brand.sudo().write({"watermark_downloads": False})
        icp = self.env["ir.config_parameter"].sudo()
        icp.set_param("bf_securetransfer.quota_daily_transfers_per_ip", "500")
        icp.set_param("bf_securetransfer.quota_daily_transfers_per_sender", "500")
        icp.set_param("bf_securetransfer.require_recipient_otp", "0")
        icp.set_param("bf_securetransfer.require_sender_otp", "0")

    def _transfer(self):
        # "fr_CA" on purpose: the product's default locale, the one that broke.
        transfer = self.env["secure.transfer"].api_create(
            self.brand, {
                "sender_name": "Test Sender",
                "sender_email": "sender@example.com",
                "recipient_emails": "dest@example.com",
                "message": "Bonjour",
                "retention_days": 7,
            }, "203.0.113.10", "test-suite/1.0", "fr_CA")
        transfer._register_file("doc.pdf", 4096)
        with patch(S3_MOD + ".head_object", return_value={"size": 4096, "etag": "etag"}), \
                patch("odoo.addons.mail.models.mail_mail.MailMail.send", lambda s, *a, **k: None):
            transfer.action_finalize()
        self.env.flush_all()
        return transfer

    def test_the_fallback_is_an_active_language(self):
        actives = [code for code, _name in self.env["res.lang"].get_installed()]
        self.assertIn(active_lang(self.env, "fr_CA"), actives)
        transfer = self._transfer()
        self.assertEqual(transfer.locale, "fr_CA", "the stored locale is unchanged")
        self.assertIn(transfer._render_lang(), actives)
        self.assertIn(transfer._lang_for_email("nobody@example.com"), actives)

    def test_the_upload_page_answers(self):
        res = self.url_open("/secrets")
        self.assertEqual(res.status_code, 200, res.text[:300])
        self.assertNotIn("Invalid language code", res.text)

    def test_the_download_page_answers(self):
        transfer = self._transfer()
        res = self.url_open("/s/%s" % transfer.sudo().token, allow_redirects=False)
        self.assertNotEqual(res.status_code, 400, res.text[:300])
        self.assertNotIn("Invalid language code", res.text)

    def test_the_certificate_renders(self):
        transfer = self._transfer()
        html, _kind = self.env["ir.actions.report"]._render_qweb_html(
            "bf_securetransfer.action_report_access_certificate", transfer.ids)
        self.assertIn(b"<", html)
