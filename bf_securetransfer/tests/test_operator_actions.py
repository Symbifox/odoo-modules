"""Backend operator actions and the Loi 25 evidence surface.

These are the buttons an administrator reaches for when something has gone
wrong: suspend a link, restore one that was wrongly reported, purge storage on
demand, resend the mails, and produce the trail a client can be shown. Their
guards are the whole point — a purge that runs one state too early destroys
evidence, and a reactivate that skips its checks hands back a link whose bytes
are already gone.

S3 is patched throughout; the suite never touches the network.
"""
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

from .common import LanguesActives

S3_MOD = "odoo.addons.bf_securetransfer.models.s3"


@tagged("post_install", "-at_install")
class TestOperatorActions(LanguesActives, TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.brand = cls.env.ref("bf_securetransfer.brand_default")
        icp = cls.env["ir.config_parameter"].sudo()
        icp.set_param("bf_securetransfer.quota_daily_transfers_per_ip", "500")
        icp.set_param("bf_securetransfer.quota_daily_transfers_per_sender", "500")
        icp.set_param("bf_securetransfer.quota_daily_bytes_per_ip_mb", "1000000")
        cls.manager_group = cls.env.ref(
            "bf_securetransfer.group_securetransfer_manager")

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

    def _head_for(self, transfer):
        sizes = {f.s3_key: int(f.size) for f in transfer.file_ids}

        def _head(env, key):
            if key in sizes:
                return {"size": sizes[key], "etag": "etag-" + key[-8:]}
            return None
        return _head

    def _active(self, **overrides):
        t = self._transfer(**overrides)
        t._register_file("doc.pdf", 4096)
        with patch(S3_MOD + ".head_object", side_effect=self._head_for(t)):
            t.action_finalize()
        return t

    # ------------------------------------------------------------------ suspend
    def test_suspend_from_active_and_from_expired(self):
        """Suspension is the abuse kill-switch. It must reach an already
        expired transfer too — evidence is preserved either way."""
        t = self._active()
        t.action_suspend()
        self.assertEqual(t.state, "suspended")
        self.assertIn("suspended", t.access_log_ids.mapped("action"))

        other = self._active()
        other.state = "expired"
        other.action_suspend()
        self.assertEqual(other.state, "suspended")

    def test_suspend_refuses_draft_and_deleted(self):
        """Suspending a draft or an already purged transfer is meaningless and
        would put the record in a state the purge cron does not expect."""
        draft = self._transfer()
        with self.assertRaises(UserError):
            draft.action_suspend()
        gone = self._active()
        gone.state = "deleted"
        with self.assertRaises(UserError):
            gone.action_suspend()

    def test_suspend_records_the_operator(self):
        """A manual suspension has to name who did it — an anonymous entry is
        useless in an audit."""
        t = self._active()
        t.action_suspend()
        entry = t.access_log_ids.filtered(lambda e: e.action == "suspended")[-1]
        self.assertEqual(entry.actor, self.env.user.login)

    # ------------------------------------------------------------------ reactivate
    def test_reactivate_refuses_once_files_are_purged(self):
        """Handing back a link whose bytes are gone would give the recipient a
        page that 404s on every file."""
        t = self._active()
        t.action_suspend()
        t.purged_at = fields.Datetime.now()
        with self.assertRaises(UserError):
            t.action_reactivate()
        self.assertEqual(t.state, "suspended")

    def test_reactivate_refuses_without_a_verified_file(self):
        """Same failure, reached the other way: no verified file left."""
        t = self._active()
        t.action_suspend()
        t.file_ids.state = "purged"
        with self.assertRaises(UserError):
            t.action_reactivate()

    def test_reactivate_refuses_past_expiry(self):
        """Reactivating past the expiry date would resurrect a link the
        retention policy already promised to close."""
        t = self._active()
        t.action_suspend()
        t.expiry_date = fields.Datetime.now() - timedelta(minutes=1)
        with self.assertRaises(UserError):
            t.action_reactivate()

    def test_reactivate_succeeds_within_expiry_with_files(self):
        """The nominal restore after a false abuse report."""
        t = self._active()
        t.action_suspend()
        t.action_reactivate()
        self.assertEqual(t.state, "active")
        self.assertIn("reactivated", t.access_log_ids.mapped("action"))

    # ------------------------------------------------------------------ purge now
    def test_purge_now_requires_the_manager_group(self):
        """This is destructive on storage. A plain securetransfer user must not
        be able to erase a client's files."""
        t = self._active()
        user = self.env["res.users"].create({
            "name": "Simple", "login": "st-simple-user",
            "groups_id": [(6, 0, [
                self.env.ref("base.group_user").id,
                self.env.ref("bf_securetransfer.group_securetransfer_user").id,
            ])],
        })
        with self.assertRaises(UserError):
            t.with_user(user).action_purge_now()

    def test_purge_now_expires_then_purges(self):
        """An active transfer is expired first so the state machine never skips
        a step — the trail must show expiry before purge."""
        t = self._active()
        self.env.user.groups_id = [(4, self.manager_group.id)]
        with patch(S3_MOD + ".delete_keys", return_value=[]), \
                patch(S3_MOD + ".mpu_abort", return_value=True):
            t.action_purge_now()
        actions = t.access_log_ids.mapped("action")
        self.assertIn("expired", actions)
        self.assertIn("purged", actions)
        self.assertEqual(t.state, "deleted")

    def test_purge_now_skips_already_deleted(self):
        """Re-purging a deleted transfer must be a no-op, not a second round of
        S3 calls."""
        t = self._active()
        self.env.user.groups_id = [(4, self.manager_group.id)]
        t.state = "deleted"
        with patch(S3_MOD + ".delete_keys", return_value=[]) as dk, \
                patch(S3_MOD + ".mpu_abort", return_value=True):
            t.action_purge_now()
        dk.assert_not_called()

    # ------------------------------------------------------------------ resend
    def test_resend_only_for_active(self):
        """Resending on an expired transfer would mail a link that is already
        dead — a support call waiting to happen."""
        self.env.user.groups_id = [(4, self.manager_group.id)]
        t = self._active()
        t.state = "expired"
        with self.assertRaises(UserError):
            t.action_resend_emails()

    def test_resend_requeues_the_emails(self):
        """The nominal 'the client says they never got it' path. Recipients
        only: the sender already holds their receipt, and re-mailing it would
        post the share link a second time into a mailbox that has it."""
        self.env.user.groups_id = [(4, self.manager_group.id)]
        t = self._active()
        with patch.object(type(t), "_send_link_emails") as send:
            t.action_resend_emails()
        send.assert_called_once()
        self.assertEqual(send.call_args.kwargs.get("recipients_only"), True)

    # ------------------------------------------------------------------ expire now
    def test_expire_now_handles_a_multi_record_set(self):
        """The list view lets an operator select several transfers; the action
        loops, so all of them must flip, not just the first."""
        a, b = self._active(), self._active()
        (a | b).action_expire_now()
        self.assertEqual(a.state, "expired")
        self.assertEqual(b.state, "expired")

    # ------------------------------------------------------------------ evidence surface
    def test_export_log_produces_a_csv_attachment(self):
        """The CSV is what a client is handed when they ask what happened to
        their file. It must exist, and it must carry the chain verdict."""
        t = self._active()
        action = t.action_export_log()
        self.assertEqual(action.get("type"), "ir.actions.act_url")
        att = self.env["ir.attachment"].sudo().search(
            [("name", "like", t.name)], order="id desc", limit=1)
        self.assertTrue(att, "no attachment was produced")
        body = att.raw.decode("utf-8-sig", "replace")
        self.assertIn(t.name, body)
        self.assertIn("finalized", body)

    def test_verify_log_chain_reports_intact(self):
        """The button an operator presses before exporting anything."""
        t = self._active()
        result = t.action_verify_log_chain()
        self.assertTrue(result, "the action must return a notification")
        self.assertTrue(t.access_log_ids[0].verify_chain())

    def test_verify_chain_detects_a_deleted_middle_entry(self):
        """Tamper-evidence is not only about edits: removing an entry from the
        middle must break the chain too, or a trail could be quietly thinned."""
        t = self._active()
        t._log("view", ip="203.0.113.5")
        t._log("download", ip="203.0.113.5")
        entries = t.access_log_ids.sorted("id")
        self.assertGreaterEqual(len(entries), 4)
        victim = entries[2]
        self.env.cr.execute(
            "DELETE FROM secure_transfer_access_log WHERE id = %s", (victim.id,))
        t.access_log_ids.invalidate_recordset()
        self.assertFalse(entries[0].verify_chain())

    def test_certificate_context_is_renderable(self):
        """The access certificate is the opposable deliverable. Its context has
        to hold the numbered lines and the chain verdict."""
        t = self._active()
        ctx = t._certificate_context()
        self.assertTrue(ctx.get("intact"))
        rows = ctx.get("rows")
        self.assertTrue(rows, "the certificate must list events")
        # rows are numbered and carry the per-entry hash a client can re-check
        self.assertEqual([r["seq"] for r in rows], list(range(1, len(rows) + 1)))
        self.assertTrue(all(r["hash"] for r in rows))
        self.assertEqual(ctx["last_hash"], rows[-1]["hash"])
        # the user agent is capped so one long header cannot wreck the layout
        self.assertTrue(all(len(r["user_agent"]) <= 60 for r in rows))

    def test_certificate_context_on_an_empty_trail(self):
        """A transfer with no entries must not make the certificate crash —
        it reports an intact, empty trail."""
        t = self._transfer()
        t.access_log_ids.with_context(st_gc=True).unlink()
        ctx = t._certificate_context()
        self.assertTrue(ctx.get("intact"))
