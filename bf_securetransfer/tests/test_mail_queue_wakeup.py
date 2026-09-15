"""The branded e-mails are queued, so the queue has to be woken up.

``send_mail(force_send=False)`` only creates mail.mail rows. Nothing sends them
until « Mail: Email Queue Manager » runs, and that cron ticks every five minutes
on a single cron worker: a transfer's link reached its recipient anywhere from
a few seconds to five and a half minutes after the sender pressed « Envoyer ».

The mails stay queued (they must leave after the commit, never before it); the
transfer now asks the cron to run right away. ``ir.cron._trigger()`` writes an
ir.cron.trigger row and notifies the cron worker from a postcommit hook. A test
transaction never commits, so the row is the observable part, and it is what
these tests count.

S3 is patched throughout; the suite never touches the network.
"""
from unittest.mock import patch

from odoo import fields
from odoo.tests import TransactionCase, tagged

from .common import BaseNeuve

S3_MOD = "odoo.addons.bf_securetransfer.models.s3"


@tagged("post_install", "-at_install")
class TestMailQueueWakeup(BaseNeuve, TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.brand = cls.env.ref("bf_securetransfer.brand_default")
        icp = cls.env["ir.config_parameter"].sudo()
        icp.set_param("bf_securetransfer.quota_daily_transfers_per_ip", "500")
        icp.set_param("bf_securetransfer.quota_daily_transfers_per_sender", "500")
        icp.set_param("bf_securetransfer.quota_daily_bytes_per_ip_mb", "1000000")
        cls.mail_cron = cls.env.ref("mail.ir_cron_mail_scheduler_action")
        # The wake-up is skipped for an inactive cron (Odoo drops immediate
        # triggers on it), so pin the state the tenants actually run.
        cls.mail_cron.sudo().active = True

    def _transfer(self, **overrides):
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

    def _active(self):
        t = self._transfer()
        t._register_file("doc.pdf", 4096)
        sizes = {f.s3_key: int(f.size) for f in t.file_ids}

        def _head(env, key):
            if key in sizes:
                return {"size": sizes[key], "etag": "etag-" + key[-8:]}
            return None
        with patch(S3_MOD + ".head_object", side_effect=_head):
            t.action_finalize()
        return t

    def _due_wakeups(self):
        return self.env["ir.cron.trigger"].sudo().search_count([
            ("cron_id", "=", self.mail_cron.id),
            ("call_at", "<=", fields.Datetime.now()),
        ])

    def _mails_of(self, transfer):
        return self.env["mail.mail"].sudo().search([
            ("model", "=", transfer._name), ("res_id", "=", transfer.id),
        ])

    def test_finalize_wakes_the_queue(self):
        before = self._due_wakeups()
        t = self._active()
        self.assertGreater(
            self._due_wakeups(), before,
            "the link and the receipt were queued without waking the mail "
            "queue: they wait for its next tick, up to five minutes")
        # Still queued, not sent inside the transaction: the SMTP talk belongs
        # to the cron, after the transfer is committed.
        mails = self._mails_of(t)
        self.assertTrue(mails, "finalize queued no e-mail at all")
        self.assertEqual(set(mails.mapped("state")), {"outgoing"})

    def test_download_notice_wakes_the_queue(self):
        t = self._active()
        before = self._due_wakeups()
        t._notify_download()
        self.assertGreater(
            self._due_wakeups(), before,
            "the download notice was queued without waking the mail queue")

    def test_resend_wakes_the_queue(self):
        self.env.user.groups_id = [(4, self.env.ref(
            "bf_securetransfer.group_securetransfer_manager").id)]
        t = self._active()
        before = self._due_wakeups()
        t.action_resend_emails()
        self.assertGreater(
            self._due_wakeups(), before,
            "a manual resend was queued without waking the mail queue")
