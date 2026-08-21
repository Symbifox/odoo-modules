"""Transfer lifecycle: draft creation, file registration guards, finalize
(S3 mocked — no network ever), cron expiry/purge, unlink guard.

Every S3 touchpoint is patched on ``odoo.addons.bf_securetransfer.models.s3``
(the single boto3 gateway): the suite must run on a build without boto3 and
without any reachable endpoint.
"""
from datetime import timedelta
import re

from unittest.mock import patch

from odoo import fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests import TransactionCase, tagged

S3_MOD = "odoo.addons.bf_securetransfer.models.s3"
MB = 1024 * 1024


@tagged("post_install", "-at_install")
class TestSecureTransferLifecycle(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.brand = cls.env.ref("bf_securetransfer.brand_default")
        icp = cls.env["ir.config_parameter"].sudo()
        # Roomy daily quotas so the suite never trips anti-abuse counters.
        icp.set_param("bf_securetransfer.quota_daily_transfers_per_ip", "500")
        icp.set_param("bf_securetransfer.quota_daily_transfers_per_sender", "500")
        icp.set_param("bf_securetransfer.quota_daily_bytes_per_ip_mb", "1000000")

    def _create(self, **overrides):
        vals = {
            "sender_name": "Test Sender",
            "sender_email": "sender@example.com",
            "recipient_emails": "dest@example.com",
            "message": "Bonjour",
            "retention_days": 7,
        }
        vals.update(overrides)
        return self.env["secure.transfer"].api_create(
            self.brand, vals, "203.0.113.10", "test-suite/1.0", "fr_CA",
        )

    def _head_for(self, transfer):
        """head_object stub returning the declared size for any of the
        transfer's keys (finalize verification passes)."""
        sizes = {f.s3_key: int(f.size) for f in transfer.file_ids}

        def _head(env, key):
            if key in sizes:
                return {"size": sizes[key], "etag": "etag-" + key[-8:]}
            return None
        return _head

    # ------------------------------------------------------------------ create
    def test_api_create_draft(self):
        t = self._create()
        self.assertEqual(t.state, "draft")
        self.assertTrue(t.name.startswith("TRF-"))
        self.assertEqual(t.sender_email, "sender@example.com")
        self.assertTrue(t.sudo().token)
        self.assertTrue(t.sudo().upload_token)
        self.assertNotEqual(t.sudo().token, t.sudo().upload_token)
        # creation is journaled
        self.assertEqual(t.access_log_ids.mapped("action"), ["created"])

    def test_email_optional_at_create_required_at_finalize(self):
        # A draft is created without a valid e-mail (so a file can be dropped
        # first); the e-mail is only required to SEND.
        t = self._create(sender_email="not-an-email")   # normalizes to empty
        self.assertFalse(t.sender_email)
        t._register_file("doc.pdf", 4096)
        with self.assertRaises(UserError):
            with patch(S3_MOD + ".head_object", side_effect=self._head_for(t)):
                t.action_finalize()

    def test_message_only_finalize(self):
        # No files, but a message (message-only mode) → finalize activates.
        t = self._create(sender_email="s@x.com")  # _create sets a message
        self.assertFalse(t.file_ids)
        res = t.action_finalize()
        self.assertIn("share_url", res)
        self.assertEqual(t.state, "active")

    def test_message_only_body_not_emailed(self):
        # A message-only transfer must NOT leak its body into the recipient's
        # inbox: the notification carries the LINK only; the message is read on
        # the branded page. (Regression guard for the message-reveal fix.)
        secret = "MOT-DE-PASSE-SECRET-42"
        Mail = self.env["mail.mail"].sudo()
        before = Mail.search([])
        t = self._create(message=secret)          # no files → message-only
        self.assertFalse(t.file_ids)
        t.action_finalize()
        to_recipient = (Mail.search([]) - before).filtered(
            lambda m: "dest@example.com" in (m.email_to or ""))
        self.assertTrue(to_recipient, "recipient notification was not queued")
        for m in to_recipient:
            self.assertNotIn(
                secret, m.body_html or "",
                "the message body leaked into the recipient email")
            self.assertIn("/s/", m.body_html or "",
                          "the share link must be present so it can be read")

    def test_file_transfer_keeps_cover_message(self):
        # With files, the message is a cover note and stays inline as before.
        secret = "NOTE-DE-COUVERTURE-XYZ"
        Mail = self.env["mail.mail"].sudo()
        before = Mail.search([])
        t = self._create(message=secret)
        t._register_file("doc.pdf", 4096)
        with patch(S3_MOD + ".head_object", side_effect=self._head_for(t)):
            t.action_finalize()
        to_recipient = (Mail.search([]) - before).filtered(
            lambda m: "dest@example.com" in (m.email_to or ""))
        self.assertTrue(to_recipient)
        self.assertTrue(
            any(secret in (m.body_html or "") for m in to_recipient),
            "the cover message should be inline for a file transfer")

    def test_finalize_needs_files_or_message(self):
        t = self._create(sender_email="s@x.com", message="")
        with self.assertRaises(UserError):
            t.action_finalize()  # neither files nor message

    # ------------------------------------------------------------ drop page
    def _drop_brand(self, **overrides):
        # The slug is UNIQUE across brands, so it must not collide with one a
        # tenant actually publishes — a personal slug did, and every drop-page test
        # errored out on the BF database instead of running.
        vals = {
            "name": "Dépôt test",
            "slug": "depot-test-unitaire",
            "fixed_recipient": "depot@example.com",
            "fixed_recipient_name": "Depot",
            "company_id": self.brand.company_id.id,
        }
        vals.update(overrides)
        return self.env["secure.transfer.brand"].create(vals)

    def test_drop_page_resolve_slug(self):
        brand = self._drop_brand()
        found = self.env["secure.transfer.brand"]._resolve_for_slug(
            "Depot-Test-Unitaire")
        self.assertEqual(found, brand)  # case-insensitive
        self.assertFalse(
            self.env["secure.transfer.brand"]._resolve_for_slug("nope"))

    def test_drop_page_forces_recipient_at_create(self):
        # A stranger tries to send to someone else — the drop page overrides
        # the recipient to its owner, no matter what was submitted.
        brand = self._drop_brand()
        t = self.env["secure.transfer"].api_create(
            brand,
            {"sender_email": "stranger@example.com",
             "recipient_emails": "victim@evil.com, other@evil.com",
             "message": "hi", "retention_days": 7},
            "203.0.113.10", "test-suite/1.0", "fr_CA")
        self.assertEqual(t.recipient_emails, "depot@example.com")

    def test_drop_page_forces_recipient_at_finalize(self):
        # Even if the record is tampered before finalize, the fixed recipient
        # is re-forced at send time.
        brand = self._drop_brand()
        t = self.env["secure.transfer"].api_create(
            brand,
            {"sender_email": "stranger@example.com", "message": "note",
             "retention_days": 7},
            "203.0.113.10", "test-suite/1.0", "fr_CA")
        t.sudo().recipient_emails = "victim@evil.com"  # tamper
        t.action_finalize()
        self.assertEqual(t.recipient_emails, "depot@example.com")
        self.assertEqual(t.state, "active")

    def test_drop_brand_slug_format_rejected(self):
        with self.assertRaises(ValidationError):
            self._drop_brand(slug="Bad Slug!")

    def test_drop_brand_bad_fixed_recipient_rejected(self):
        with self.assertRaises(ValidationError):
            self._drop_brand(fixed_recipient="not-an-email")

    def test_api_create_rejects_retention_not_offered(self):
        # free tier caps retention at 7 days -> 30 is not a valid choice
        with self.assertRaises(UserError):
            self._create(retention_days=30)

    def test_orm_rejects_retention_not_offered(self):
        """Le contrôle de rétention doit tenir HORS du chemin public.

        ⚠ Il ne vivait que dans `api_create()` : l'assistant d'envoi interne, le
        `write` de finalisation et tout appel ORM/XML-RPC pouvaient poser une
        durée que la marque n'offre pas — et que la règle de cycle de vie du seau
        S3, écrite d'après le maximum de la marque, ne tiendrait pas.
        """
        transfer = self._create()
        with self.assertRaises(ValidationError):
            transfer.write({"retention_days": 30})

    def test_orm_accepts_offered_retention(self):
        """Le durcissement ne doit pas refuser une durée légitime."""
        transfer = self._create()
        choices = transfer.brand_id._effective_limits()["expiry_choices"]
        transfer.write({"retention_days": choices[0]})
        self.assertEqual(transfer.retention_days, choices[0])

    def test_api_create_sender_quota(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_securetransfer.quota_daily_transfers_per_sender", "2",
        )
        self._create(sender_email="quota@example.com")
        self._create(sender_email="quota@example.com")
        with self.assertRaises(UserError):
            self._create(sender_email="quota@example.com")

    # ------------------------------------------------------------------ files
    def test_register_file_ok(self):
        t = self._create()
        f = t._register_file("Rapport final.pdf", 5 * MB)
        self.assertEqual(f.state, "pending")
        self.assertEqual(f.extension, "pdf")
        self.assertEqual(f.upload_mode, "simple")
        self.assertTrue(re.match(r"transfers[^/]*/%s/" % t.s3_prefix, f.s3_key))
        self.assertNotIn("Rapport", f.s3_key)  # opaque key, no PII
        self.assertEqual(f.mimetype, "application/pdf")
        self.assertEqual(t.total_size, 5 * MB)

    def test_register_file_multipart_over_threshold(self):
        t = self._create()
        f = t._register_file("gros.zip", 100 * MB)  # threshold default 64 MB
        self.assertEqual(f.upload_mode, "multipart")

    def test_register_file_deny_list(self):
        t = self._create()
        for name in ("evil.exe", "page.html", "script.js", "shell.sh"):
            with self.assertRaises(UserError, msg=name):
                t._register_file(name, 1024)

    def test_register_file_requires_extension(self):
        t = self._create()
        with self.assertRaises(UserError):
            t._register_file("sans_extension", 1024)

    def test_register_file_strips_path_components(self):
        t = self._create()
        f = t._register_file("..\\dossier/é rapport.pdf", 1024)
        self.assertEqual(f.filename, "é rapport.pdf")

    def test_register_file_rejects_bad_size(self):
        t = self._create()
        with self.assertRaises(UserError):
            t._register_file("a.pdf", 0)
        with self.assertRaises(UserError):
            t._register_file("a.pdf", "abc")

    def test_register_file_oversize_refused(self):
        small = self.env["secure.transfer.brand"].create({
            "name": "Petite marque", "domain": "small.example.test",
            "max_transfer_mb": 1,
        })
        t = self.env["secure.transfer"].api_create(
            small, {"sender_email": "s@example.com", "retention_days": 1},
            "203.0.113.11", "t", "fr_CA",
        )
        with self.assertRaises(UserError):
            t._register_file("trop-gros.pdf", 2 * MB)

    def test_register_file_max_files(self):
        capped = self.env["secure.transfer.brand"].create({
            "name": "Capped", "domain": "capped.example.test", "max_files": 2,
        })
        t = self.env["secure.transfer"].api_create(
            capped, {"sender_email": "s2@example.com", "retention_days": 1},
            "203.0.113.12", "t", "fr_CA",
        )
        t._register_file("a.pdf", 1024)
        t._register_file("b.pdf", 1024)
        with self.assertRaises(UserError):
            t._register_file("c.pdf", 1024)

    # ------------------------------------------------------------------ finalize
    def test_finalize_activates_and_rotates_token(self):
        t = self._create()
        t._register_file("doc.pdf", 4096)
        old_upload_token = t.sudo().upload_token
        with patch(S3_MOD + ".head_object", side_effect=self._head_for(t)):
            res = t.action_finalize()
        self.assertEqual(t.state, "active")
        self.assertNotEqual(t.sudo().upload_token, old_upload_token)
        self.assertIn("/s/" + t.sudo().token, res["share_url"])
        self.assertTrue(t.expiry_date)
        self.assertEqual(t.file_ids.state, "verified")
        self.assertTrue(t.file_ids.etag)
        actions = t.access_log_ids.mapped("action")
        self.assertIn("finalized", actions)
        self.assertIn("emailed", actions)

    def test_finalize_idempotent(self):
        t = self._create()
        t._register_file("doc.pdf", 4096)
        with patch(S3_MOD + ".head_object", side_effect=self._head_for(t)):
            first = t.action_finalize()
            second = t.action_finalize()
        self.assertEqual(first["share_url"], second["share_url"])
        self.assertEqual(
            t.access_log_ids.mapped("action").count("finalized"), 1,
        )

    def test_finalize_refuses_missing_object(self):
        t = self._create()
        t._register_file("doc.pdf", 4096)
        with patch(S3_MOD + ".head_object", return_value=None):
            with self.assertRaises(UserError):
                t.action_finalize()
        # The meaningful, durable invariant: finalize refused, the transfer
        # stays draft and the file is NOT verified. (The transient "error" mark
        # written by _verify_on_s3 is rolled back with the failing request in
        # production, so we don't assert on it.)
        t.invalidate_recordset()
        self.assertEqual(t.state, "draft")
        self.assertNotEqual(t.file_ids.state, "verified")

    def test_finalize_refuses_size_mismatch(self):
        t = self._create()
        t._register_file("doc.pdf", 4096)
        with patch(
            S3_MOD + ".head_object",
            return_value={"size": 999, "etag": "x"},
        ):
            with self.assertRaises(UserError):
                t.action_finalize()

    def test_finalize_with_password(self):
        t = self._create()
        t._register_file("doc.pdf", 4096)
        with patch(S3_MOD + ".head_object", side_effect=self._head_for(t)):
            t.action_finalize(password="s3cret!")
        self.assertTrue(t.has_password)
        self.assertTrue(t._check_password("s3cret!"))
        self.assertFalse(t._check_password("wrong"))

    # ------------------------------------------------------------------ availability / download
    def _active_transfer(self, **overrides):
        t = self._create(**overrides)
        t._register_file("doc.pdf", 4096)
        with patch(S3_MOD + ".head_object", side_effect=self._head_for(t)):
            t.action_finalize()
        return t

    def test_availability_reasons(self):
        t = self._create()
        self.assertEqual(t._is_available(), (False, "not_found"))
        t = self._active_transfer()
        self.assertEqual(t._is_available(), (True, ""))
        t.state = "suspended"
        self.assertEqual(t._is_available(), (False, "suspended"))
        t.state = "active"
        t.expiry_date = fields.Datetime.now() - timedelta(minutes=1)
        self.assertEqual(t._is_available(), (False, "expired"))

    def test_download_budget_expires_transfer(self):
        t = self._active_transfer(max_downloads=1)
        f = t.file_ids
        t._register_download(f, "203.0.113.99", "dl-agent")
        self.assertEqual(t.download_count, 1)
        self.assertEqual(f.download_count, 1)
        self.assertEqual(t.state, "expired")
        with self.assertRaises(UserError):
            t._register_download(f, "203.0.113.99", "dl-agent")

    def _burning_multi_file_transfer(self, count=2):
        """Active burn-after-download transfer carrying `count` files."""
        # burn is not an api_create val — the upload API sets it at finalize
        t = self._create()
        t.burn_after_download = True
        for i in range(count):
            t._register_file("doc%d.pdf" % i, 4096)
        with patch(S3_MOD + ".head_object", side_effect=self._head_for(t)):
            t.action_finalize()
        return t

    def test_burn_waits_for_every_file(self):
        """A browser fetches one file per request: burning on the first
        download strands the rest of the transfer behind a dead link."""
        t = self._burning_multi_file_transfer(2)
        first, second = t.file_ids[0], t.file_ids[1]
        t._register_download(first, "203.0.113.99", "dl-agent")
        # still alive — the recipient does not have the second file yet
        self.assertEqual(t.state, "active")
        self.assertEqual(t._is_available(), (True, ""))
        t._register_download(second, "203.0.113.99", "dl-agent")
        self.assertEqual(t.state, "expired")
        self.assertIn("expired", t.access_log_ids.mapped("action"))

    def test_burn_single_file_still_immediate(self):
        t = self._burning_multi_file_transfer(1)
        t._register_download(t.file_ids, "203.0.113.99", "dl-agent")
        self.assertEqual(t.state, "expired")

    def test_burn_ignores_undownloadable_files(self):
        """An infected or errored file was never offered — it must not hold
        the burn open forever."""
        t = self._burning_multi_file_transfer(2)
        good, bad = t.file_ids[0], t.file_ids[1]
        bad.scanned = "infected"
        t._register_download(good, "203.0.113.99", "dl-agent")
        self.assertEqual(t.state, "expired")

    def test_burn_repeat_download_does_not_burn_early(self):
        """Re-fetching the same file (a retried/interrupted download) must not
        count as having collected the other one."""
        t = self._burning_multi_file_transfer(2)
        first = t.file_ids[0]
        t._register_download(first, "203.0.113.99", "dl-agent")
        t._register_download(first, "203.0.113.99", "dl-agent")
        self.assertEqual(t.state, "active")
        self.assertEqual(first.download_count, 2)

    # ------------------------------------------------------------------ filename handling
    def test_non_latin_filename_keeps_its_extension(self):
        """secure_filename drops non-ASCII then strips "._", so "документ.pdf"
        collapsed to "pdf" — no dot left — and a legitimate file was refused
        for "having no extension"."""
        t = self._create()
        for name in ("документ.pdf", "报告.pdf", "Rapport-Été.pdf"):
            f = t._register_file(name, 4096)
            self.assertEqual(f.extension, "pdf", "refused/mis-parsed: %s" % name)
            self.assertEqual(f.filename, name, "display name must keep Unicode")

    def test_non_latin_filename_still_hits_the_deny_list(self):
        """Reading the extension from the raw name must not open a hole: it
        actually closes one, since the deny-list now sees the true suffix
        instead of an empty string."""
        t = self._create()
        with self.assertRaises(UserError) as ctx:
            t._register_file("рисунок.php", 4096)
        # refused BY RULE (format not allowed), not incidentally
        self.assertIn("php", str(ctx.exception))

    def test_filename_without_extension_still_refused(self):
        t = self._create()
        with self.assertRaises(UserError):
            t._register_file("facture", 4096)

    # ------------------------------------------------------------------ recipient OTP gate
    def _sent_bodies(self, transfer):
        """Bodies of the link notifications queued for a transfer."""
        return self.env["mail.mail"].sudo().search(
            [("email_to", "in", transfer._recipient_list())]).mapped("body_html")

    def test_instance_wide_otp_holds_the_message_out_of_the_email(self):
        """The instance-wide setting must pick the same template the
        per-transfer flag does. Testing only the flag left the message body
        and the filename listing in the recipient's inbox — the gate held the
        files, but the notification had already given away the content."""
        icp = self.env["ir.config_parameter"].sudo()
        icp.set_param("bf_securetransfer.require_recipient_otp", "1")
        self.addCleanup(
            icp.set_param, "bf_securetransfer.require_recipient_otp", "0")
        t = self._create(message="Le NIP du dossier est 4417")
        t._register_file("contrat-secret.pdf", 4096)
        with patch(S3_MOD + ".head_object", side_effect=self._head_for(t)), \
                patch("odoo.addons.mail.models.mail_mail.MailMail.send",
                      lambda self, *a, **k: True):
            t.action_finalize()
        self.assertTrue(t._recipient_otp_required())
        bodies = "".join(self._sent_bodies(t))
        self.assertTrue(bodies, "no notification was queued")
        self.assertNotIn("4417", bodies)
        self.assertNotIn("contrat-secret.pdf", bodies)

    def test_without_otp_the_link_email_still_carries_the_message(self):
        """Guard against over-correcting: with no gate, the ordinary link
        template is still the one used."""
        t = self._create(message="Bonjour, voici les documents")
        t._register_file("rapport.pdf", 4096)
        with patch(S3_MOD + ".head_object", side_effect=self._head_for(t)), \
                patch("odoo.addons.mail.models.mail_mail.MailMail.send",
                      lambda self, *a, **k: True):
            t.action_finalize()
        self.assertFalse(t._recipient_otp_required())
        bodies = "".join(self._sent_bodies(t))
        self.assertIn("rapport.pdf", bodies)

    # ------------------------------------------------------------------ quotas
    def test_sender_quota_does_not_count_the_transfer_being_finalized(self):
        """At finalize the transfer is already in the DB, so counting it
        against its own quota made the Nth send fail — AFTER the upload."""
        icp = self.env["ir.config_parameter"].sudo()
        icp.set_param("bf_securetransfer.quota_daily_transfers_per_sender", "2")
        self.addCleanup(
            icp.set_param,
            "bf_securetransfer.quota_daily_transfers_per_sender", "500")
        # 1st transfer of the day: creates and finalizes cleanly
        first = self._active_transfer()
        self.assertEqual(first.state, "active")
        # 2nd: the quota is 2, so this one must go through as well
        second = self._create()
        second._register_file("doc.pdf", 4096)
        with patch(S3_MOD + ".head_object", side_effect=self._head_for(second)):
            second.action_finalize()
        self.assertEqual(second.state, "active")

    def test_sender_quota_still_refuses_past_the_budget(self):
        """The exclusion must not disarm the quota — the 3rd of a budget of 2
        is still refused."""
        icp = self.env["ir.config_parameter"].sudo()
        icp.set_param("bf_securetransfer.quota_daily_transfers_per_sender", "2")
        self.addCleanup(
            icp.set_param,
            "bf_securetransfer.quota_daily_transfers_per_sender", "500")
        self._active_transfer()
        self._active_transfer()
        with self.assertRaises(UserError):
            self._create()

    # ------------------------------------------------------------------ cron expiry + purge
    def test_cron_purge_expired(self):
        t = self._active_transfer()
        t.expiry_date = fields.Datetime.now() - timedelta(days=1)
        with patch(S3_MOD + ".delete_keys", return_value=[]) as dk, \
                patch(S3_MOD + ".mpu_abort", return_value=True):
            self.env["secure.transfer"]._cron_purge_expired()
        self.assertEqual(t.state, "deleted")
        self.assertTrue(t.purged_at)
        self.assertEqual(t.file_ids.state, "purged")
        # its S3 keys were handed to delete_keys
        purged_keys = [k for call in dk.call_args_list for k in call.args[1]]
        self.assertIn(t.file_ids.s3_key, purged_keys)
        actions = t.access_log_ids.mapped("action")
        self.assertIn("expired", actions)
        self.assertIn("purged", actions)
        # metadata and trail survive the purge (Loi 25)
        self.assertTrue(t.exists())
        self.assertTrue(t.access_log_ids)

    def test_purge_failure_counts(self):
        t = self._active_transfer()
        t.action_expire_now()
        key = t.file_ids.s3_key
        with patch(S3_MOD + ".delete_keys", return_value=[key]):
            ok = t._purge_s3()
        self.assertFalse(ok)
        self.assertEqual(t.state, "expired")  # not flipped on failure
        self.assertEqual(t.purge_error_count, 1)

    def test_operator_expire_only_active(self):
        t = self._create()
        with self.assertRaises(UserError):
            t.action_expire_now()

    # ------------------------------------------------------------------ sender OTP
    def test_sender_otp_flow(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_securetransfer.require_sender_otp", "1")
        t = self._create()
        t._register_file("a.pdf", 4096)
        captured = {}

        def fake_email(rec, to, code, intro):
            captured["code"] = code

        with patch(S3_MOD + ".head_object", side_effect=self._head_for(t)), \
                patch.object(type(t), "_otp_email", fake_email):
            res = t.action_finalize()
            self.assertEqual(res.get("otp_required"), "sender")
            self.assertEqual(t.state, "draft")           # NOT activated yet
            self.assertTrue(t.sudo().sender_otp_hash)
            # wrong code is refused
            bad = "000000" if captured["code"] != "000000" else "111111"
            with self.assertRaises(UserError):
                t.confirm_sender_otp(bad)
            # right code activates and clears the OTP
            res2 = t.confirm_sender_otp(captured["code"])
            self.assertIn("share_url", res2)
            self.assertEqual(t.state, "active")
            self.assertTrue(t.sender_confirmed)
            self.assertFalse(t.sudo().sender_otp_hash)

    def test_resend_does_not_refill_the_attempt_budget(self):
        """"Renvoyer un code" used to reset sender_otp_fails, so seven wrong
        guesses plus one resend restored the full budget and the 8-attempt
        ceiling bounded nothing."""
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_securetransfer.require_sender_otp", "1")
        t = self._create()
        t._register_file("a.pdf", 4096)
        with patch(S3_MOD + ".head_object", side_effect=self._head_for(t)), \
                patch.object(type(t), "_otp_email", lambda *a, **k: None):
            res = t.action_finalize()
            self.assertEqual(res.get("otp_required"), "sender",
                             "finalize did not enter the sender-OTP path")
            self.assertTrue(t.sudo().sender_otp_hash)
            # NB: plain try/except, NOT assertRaises — Odoo's assertRaises
            # rolls the block back to a savepoint, which would discard the
            # very counter this test is about.
            for _i in range(3):
                try:
                    t.confirm_sender_otp("000000")
                    self.fail("a wrong code must be refused")
                except UserError as exc:
                    self.assertIn("invalide", str(exc).lower())
            self.assertEqual(t.sudo().sender_otp_fails, 3)
            # the resend path must PRESERVE the counter…
            t._send_sender_otp(reset_fails=False)
            self.assertEqual(t.sudo().sender_otp_fails, 3)
            # …while a genuine new send still starts clean
            t._send_sender_otp()
            self.assertEqual(t.sudo().sender_otp_fails, 0)

    def test_sender_otp_off_by_default(self):
        t = self._create()
        t._register_file("a.pdf", 4096)
        with patch(S3_MOD + ".head_object", side_effect=self._head_for(t)):
            res = t.action_finalize()
        self.assertNotIn("otp_required", res)
        self.assertEqual(t.state, "active")

    # ------------------------------------------------------------------ recipient OTP
    def test_recipient_otp_email_validation(self):
        t = self._create()  # recipient = dest@example.com
        self.assertTrue(t._is_recipient_email("dest@example.com"))
        self.assertTrue(t._is_recipient_email("DEST@Example.com"))  # normalized
        self.assertFalse(t._is_recipient_email("stranger@example.com"))
        with patch.object(type(t), "_otp_email", lambda *a, **k: None):
            h, exp = t._send_recipient_otp("dest@example.com")
            self.assertTrue(h)
            self.assertTrue(exp)
            h2, exp2 = t._send_recipient_otp("stranger@example.com")
            self.assertIsNone(h2)
            self.assertIsNone(exp2)

    # ------------------------------------------------------------------ abuse suspend
    def _active_with_file(self):
        t = self._create()
        t._register_file("doc.pdf", 4096)
        with patch(S3_MOD + ".head_object", side_effect=self._head_for(t)):
            t.action_finalize()
        return t

    def test_abuse_report_suspends_and_notifies(self):
        t = self._active_with_file()
        self.assertTrue(t._is_available()[0])
        # Le bureau d'abus est un réglage du locataire : on le pose ici plutôt
        # que d'attendre une adresse en dur (il n'y en a plus depuis 1.17.2).
        icp = self.env["ir.config_parameter"].sudo()
        icp.set_param("bf_securetransfer.abuse_email", "abus@exemple.invalid")
        self.addCleanup(icp.set_param, "bf_securetransfer.abuse_email", "")
        Mail = self.env["mail.mail"].sudo()
        before = Mail.search_count([])
        t._suspend_for_abuse(ip="203.0.113.1")
        # Stub send() so the auto_delete mails persist for counting.
        with patch("odoo.addons.mail.models.mail_mail.MailMail.send",
                   lambda self, *a, **k: True):
            t._send_abuse_notice(reason="malware", ip="203.0.113.1")
        # suspended → unavailable
        self.assertEqual(t.state, "suspended")
        self.assertFalse(t._is_available()[0])
        self.assertEqual(t._is_available()[1], "suspended")
        # two emails: abuse desk + recipients
        self.assertGreaterEqual(Mail.search_count([]) - before, 2)
        self.assertTrue(Mail.search_count([("email_to", "=", "abus@exemple.invalid")]))
        # 'suspended' is in the log
        self.assertIn("suspended", t.access_log_ids.mapped("action"))

    def test_abuse_notice_is_one_mail_per_recipient(self):
        """A single mail carrying the whole recipient list in To: would show
        every recipient to every other — triggerable by any anonymous link
        holder clicking "report abuse"."""
        t = self._create(recipient_emails="a@x.test, b@y.test, c@z.test")
        t._register_file("doc.pdf", 4096)
        with patch(S3_MOD + ".head_object", side_effect=self._head_for(t)):
            t.action_finalize()
        Mail = self.env["mail.mail"].sudo()
        t._suspend_for_abuse(ip="203.0.113.1")
        with patch("odoo.addons.mail.models.mail_mail.MailMail.send",
                   lambda self, *a, **k: True):
            t._send_abuse_notice(reason="malware", ip="203.0.113.1")
        notices = Mail.search([("subject", "like", "Transfert suspendu%")])
        self.assertEqual(len(notices), 3)
        self.assertEqual(
            sorted(notices.mapped("email_to")),
            ["a@x.test", "b@y.test", "c@z.test"])
        # no notice may name more than its own addressee
        for mail in notices:
            self.assertNotIn(",", mail.email_to or "")

    def test_abuse_suspend_idempotent_and_reactivate(self):
        t = self._active_with_file()
        t._suspend_for_abuse()
        t._suspend_for_abuse()  # idempotent — still one suspended state
        self.assertEqual(t.state, "suspended")
        t.action_reactivate()
        self.assertEqual(t.state, "active")
        self.assertTrue(t._is_available()[0])
        self.assertIn("reactivated", t.access_log_ids.mapped("action"))

    def test_reactivate_only_suspended(self):
        t = self._active_with_file()
        with self.assertRaises(UserError):
            t.action_reactivate()  # active, not suspended

    # ------------------------------------------------------------------ unlink guard
    def test_unlink_guarded(self):
        t = self._create()
        with self.assertRaises(UserError):
            t.unlink()
        self.assertTrue(t.exists())
        t.with_context(st_gc=True).unlink()
        self.assertFalse(t.exists())
