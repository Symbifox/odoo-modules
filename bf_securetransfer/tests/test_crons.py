"""The three scheduled jobs of the module, and the purge primitive they all
lean on.

``_cron_gc_logs`` is the ONLY code path in the module that ever destroys a
transfer and its access trail (the ``st_gc`` unlink). ``_cron_gc_drafts``
sweeps the bucket and deletes objects it believes nobody claims. Both are one
bad domain away from shredding a client's files or their Loi 25 evidence, and
neither had a test — hence this file.

Every S3 touchpoint is patched on ``odoo.addons.bf_securetransfer.models.s3``
(the single boto3 gateway): the suite must run on a build without boto3 and
without any reachable endpoint. No network, ever.
"""
from datetime import timedelta
from xml.etree import ElementTree
import re
import uuid

from unittest.mock import Mock, patch

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged
from odoo.tools import file_open

from odoo.addons.bf_securetransfer.models import s3
from odoo.addons.bf_securetransfer.models.secure_transfer import (
    MPU_SWEEP_GRACE_HOURS,
)

S3_MOD = "odoo.addons.bf_securetransfer.models.s3"
MB = 1024 * 1024

# The documented defaults of the two TTL parameters. Pinned explicitly so the
# suite reads the same numbers on a tenant that has overridden them.
DRAFT_TTL_HOURS = 24
LOG_RETENTION_DAYS = 365


def _endpoint_error():
    """A genuine botocore endpoint failure, so ``is_endpoint_error`` is
    exercised for real rather than mocked into agreeing with us."""
    from botocore.exceptions import EndpointConnectionError
    return EndpointConnectionError(endpoint_url="https://s3.invalid.test")


@tagged("post_install", "-at_install")
class TestSecureTransferCrons(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.brand = cls.env.ref("bf_securetransfer.brand_default")
        icp = cls.env["ir.config_parameter"].sudo()
        # Roomy daily quotas so the suite never trips anti-abuse counters.
        icp.set_param("bf_securetransfer.quota_daily_transfers_per_ip", "500")
        icp.set_param("bf_securetransfer.quota_daily_transfers_per_sender", "500")
        icp.set_param("bf_securetransfer.quota_daily_bytes_per_ip_mb", "1000000")
        # Pin the two TTLs to their documented defaults.
        icp.set_param("bf_securetransfer.draft_ttl_hours", str(DRAFT_TTL_HOURS))
        icp.set_param(
            "bf_securetransfer.log_retention_days", str(LOG_RETENTION_DAYS))

    # ------------------------------------------------------------- fixtures
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
        sizes = {f.s3_key: int(f.size) for f in transfer.file_ids}

        def _head(env, key):
            if key in sizes:
                return {"size": sizes[key], "etag": "etag-" + key[-8:]}
            return None
        return _head

    def _active_transfer(self, **overrides):
        t = self._create(**overrides)
        t._register_file("doc.pdf", 4096)
        with patch(S3_MOD + ".head_object", side_effect=self._head_for(t)):
            t.action_finalize()
        return t

    def _deleted_transfer(self):
        """An active transfer taken all the way to ``deleted`` through the
        real purge path (so purged_at is set the way production sets it)."""
        t = self._active_transfer()
        t.action_expire_now()
        with patch(S3_MOD + ".delete_keys", return_value=[]), \
                patch(S3_MOD + ".mpu_abort", return_value=True):
            t._purge_s3()
        self.assertEqual(t.state, "deleted")
        return t

    def _backdate(self, record, **fields_to_set):
        """Force ``create_date`` / ``write_date`` in SQL — the ORM owns both
        and silently overwrites anything we hand it."""
        allowed = {"create_date", "write_date"}
        assert set(fields_to_set) <= allowed, "refuse to SQL-write %s" % (
            set(fields_to_set) - allowed)
        self.env.flush_all()
        for name, value in fields_to_set.items():
            self.env.cr.execute(
                "UPDATE secure_transfer SET %s = %%s WHERE id = %%s" % name,
                (value, record.id),
            )
        record.invalidate_recordset()

    def _keys_deleted(self, delete_keys_mock):
        """Flatten every key handed to delete_keys across all its calls.

        Membership assertions on this set, rather than call counts, keep the
        tests honest on a tenant database that already holds unrelated stale
        drafts the cron will legitimately also process."""
        return {k for call in delete_keys_mock.call_args_list
                for k in call.args[1]}

    # =================================================================
    #  _purge_s3
    # =================================================================
    def test_purge_aborts_mpus_before_deleting(self):
        """Deleting the key of a live multipart upload leaves the parts
        orphaned and billable forever: S3 only frees them on AbortMultipart.
        The abort has to come first, on the right key and the right UploadId."""
        t = self._create()
        f = t._register_file("gros.zip", 100 * MB)
        upload_id = "upl-" + uuid.uuid4().hex
        f.sudo().write({"s3_upload_id": upload_id})

        manager = Mock()
        with patch(S3_MOD + ".mpu_abort", return_value=True) as abort, \
                patch(S3_MOD + ".delete_keys", return_value=[]) as delete:
            manager.attach_mock(abort, "mpu_abort")
            manager.attach_mock(delete, "delete_keys")
            self.assertTrue(t._purge_s3())

        self.assertEqual(
            [c[0] for c in manager.mock_calls],
            ["mpu_abort", "delete_keys"],
            "the pending MPU must be aborted BEFORE its key is deleted",
        )
        self.assertEqual(abort.call_args.args[1], f.s3_key)
        self.assertEqual(abort.call_args.args[2], upload_id)
        self.assertIn(f.s3_key, delete.call_args.args[1])
        # the dead upload id is cleared, so a retry cannot re-abort it
        self.assertFalse(f.s3_upload_id)

    def test_purge_of_a_draft_cancels_it_not_deletes_it(self):
        """A harvested draft was never sent. Marking it « Supprimé » would put
        transfers that never existed for anyone into the client's deletion
        statistics and Loi 25 reporting."""
        t = self._create()
        t._register_file("doc.pdf", 4096)
        with patch(S3_MOD + ".delete_keys", return_value=[]), \
                patch(S3_MOD + ".mpu_abort", return_value=True):
            self.assertTrue(t._purge_s3())
        self.assertEqual(t.state, "cancelled")
        self.assertNotEqual(t.state, "deleted")
        self.assertTrue(t.purged_at)
        purged = t.access_log_ids.filtered(lambda e: e.action == "purged")
        self.assertTrue(purged, "the purge was not journaled")
        self.assertIn("Brouillon abandonné", purged[-1].note or "")

    def test_purge_skips_already_purged_files(self):
        """Re-purging must not re-submit keys that are already gone: on a
        bucket with versioning or object-lock those calls cost money and log
        noise, and a NoSuchKey storm would look like a real failure."""
        t = self._active_transfer()
        t.action_expire_now()
        t.file_ids.sudo().write({"state": "purged"})
        with patch(S3_MOD + ".delete_keys", return_value=[]) as delete, \
                patch(S3_MOD + ".mpu_abort", return_value=True) as abort:
            ok = t._purge_s3()
        self.assertTrue(ok, "nothing left to delete is a SUCCESS, not a failure")
        self.assertFalse(delete.called, "an already-purged key was re-submitted")
        self.assertFalse(abort.called)
        self.assertEqual(t.state, "deleted")

    def test_purge_failure_raises_admin_activity_once_at_five(self):
        """The alert must fire exactly once. Every failed pass scheduling an
        activity would bury the admin's To-do list under one entry per hour
        per stuck transfer — and the real incident with it."""
        t = self._active_transfer()
        t.action_expire_now()
        key = t.file_ids.s3_key
        Activity = self.env["mail.activity"].sudo()
        domain = [("res_model", "=", "secure.transfer"), ("res_id", "=", t.id)]

        counts = []
        with patch(S3_MOD + ".delete_keys", return_value=[key]), \
                patch(S3_MOD + ".mpu_abort", return_value=True):
            for _i in range(6):
                self.assertFalse(t._purge_s3())
                counts.append(Activity.search_count(domain))

        self.assertEqual(t.purge_error_count, 6)
        # failures 1-4: silent; 5th: exactly one activity; 6th: still one
        self.assertEqual(counts, [0, 0, 0, 0, 1, 1], "activity spam or no alert")
        act = Activity.search(domain)
        self.assertEqual(act.user_id, self.env.ref("base.user_admin"))
        self.assertIn(t.name, act.summary or "")
        # a failed purge never flips the state — the files are still up there
        self.assertEqual(t.state, "expired")

    # =================================================================
    #  _cron_purge_expired
    # =================================================================
    def test_cron_purge_leaves_live_transfers_alone(self):
        """The cron deletes S3 objects. A domain slip here wipes the files of
        every client whose transfer is still within its retention window."""
        t = self._active_transfer()
        t.expiry_date = fields.Datetime.now() + timedelta(days=5)
        with patch(S3_MOD + ".delete_keys", return_value=[]) as delete, \
                patch(S3_MOD + ".mpu_abort", return_value=True):
            self.env["secure.transfer"]._cron_purge_expired()
        self.assertEqual(t.state, "active")
        self.assertFalse(t.purged_at)
        self.assertEqual(t.file_ids.state, "verified")
        self.assertNotIn(t.file_ids.s3_key, self._keys_deleted(delete))

    def test_cron_purge_survives_s3_not_configured(self):
        """On a fresh install S3 has no credentials yet. An uncaught UserError
        marks the cron job as failed, and Odoo eventually deactivates it — the
        nightly purge would then silently stop running for good."""
        t = self._active_transfer()
        t.expiry_date = fields.Datetime.now() - timedelta(days=1)
        err = UserError("Stockage S3 non configuré")
        with patch(S3_MOD + ".delete_keys", side_effect=err), \
                patch(S3_MOD + ".mpu_abort", side_effect=err):
            self.env["secure.transfer"]._cron_purge_expired()   # must not raise
        # the expiry flip is kept, the purge simply did not happen
        self.assertEqual(t.state, "expired")
        self.assertFalse(t.purged_at)

    def test_cron_purge_aborts_cleanly_on_endpoint_error_and_resumes(self):
        """A blip on the storage endpoint must not burn purge_error_count on
        every transfer in the queue — after five blips they would all raise a
        false « purge en échec répété » alert. The run stops; the next pass
        finishes the job."""
        t = self._active_transfer()
        t.expiry_date = fields.Datetime.now() - timedelta(days=1)
        with patch(S3_MOD + ".delete_keys", side_effect=_endpoint_error()), \
                patch(S3_MOD + ".mpu_abort", return_value=True):
            self.env["secure.transfer"]._cron_purge_expired()   # must not raise
        self.assertEqual(t.state, "expired")
        self.assertEqual(t.purge_error_count, 0, "an outage is not a purge failure")

        # next pass, endpoint back: the transfer is picked up where it stopped
        with patch(S3_MOD + ".delete_keys", return_value=[]), \
                patch(S3_MOD + ".mpu_abort", return_value=True):
            self.env["secure.transfer"]._cron_purge_expired()
        self.assertEqual(t.state, "deleted")
        self.assertEqual(t.file_ids.state, "purged")

    def test_cron_purge_reraises_unexpected_errors(self):
        """Swallowing everything would turn a bucket-policy or permissions
        regression into a purge that reports success forever while the client's
        files quietly stay online past their retention."""
        t = self._active_transfer()
        t.expiry_date = fields.Datetime.now() - timedelta(days=1)
        with patch(S3_MOD + ".delete_keys", side_effect=RuntimeError("boom")), \
                patch(S3_MOD + ".mpu_abort", return_value=True):
            with self.assertRaises(RuntimeError):
                self.env["secure.transfer"]._cron_purge_expired()

    def test_cron_purge_is_idempotent(self):
        """The job is retried after a killed worker and runs every night. A
        second pass re-purging an already-purged transfer would re-stamp
        purged_at and append a duplicate « purged » entry to a log that is
        supposed to be the opposable record of when deletion happened."""
        t = self._active_transfer()
        t.expiry_date = fields.Datetime.now() - timedelta(days=1)
        with patch(S3_MOD + ".delete_keys", return_value=[]), \
                patch(S3_MOD + ".mpu_abort", return_value=True):
            self.env["secure.transfer"]._cron_purge_expired()
        first_purged_at = t.purged_at
        purge_entries = t.access_log_ids.mapped("action").count("purged")
        self.assertEqual(t.state, "deleted")

        with patch(S3_MOD + ".delete_keys", return_value=[]) as delete, \
                patch(S3_MOD + ".mpu_abort", return_value=True):
            self.env["secure.transfer"]._cron_purge_expired()
        self.assertEqual(t.state, "deleted")
        self.assertEqual(t.purged_at, first_purged_at)
        self.assertEqual(
            t.access_log_ids.mapped("action").count("purged"), purge_entries)
        self.assertNotIn(t.file_ids.s3_key, self._keys_deleted(delete))

    # =================================================================
    #  _cron_gc_drafts
    # =================================================================
    def test_gc_drafts_harvests_stale_draft(self):
        """An abandoned draft holds paid storage no one will ever download,
        outside every retention rule — it is invisible to the expiry purge
        because it has no expiry_date."""
        t = self._create()
        t._register_file("doc.pdf", 4096)
        self._backdate(t, create_date=fields.Datetime.now() - timedelta(
            hours=DRAFT_TTL_HOURS + 1))
        with patch(S3_MOD + ".delete_keys", return_value=[]) as delete, \
                patch(S3_MOD + ".mpu_abort", return_value=True), \
                patch(S3_MOD + ".list_stale_mpus", return_value=[]), \
                patch(S3_MOD + ".list_objects", return_value=[]):
            self.env["secure.transfer"]._cron_gc_drafts()
        self.assertEqual(t.state, "cancelled")
        self.assertEqual(t.file_ids.state, "purged")
        self.assertIn(t.file_ids.s3_key, self._keys_deleted(delete))

    def test_gc_drafts_spares_a_fresh_draft(self):
        """A sender fills the form, then goes to lunch mid-upload. Harvesting
        a draft still inside its TTL cancels a transfer while the browser is
        still PUTting parts to it."""
        t = self._create()
        t._register_file("doc.pdf", 4096)
        with patch(S3_MOD + ".delete_keys", return_value=[]) as delete, \
                patch(S3_MOD + ".mpu_abort", return_value=True), \
                patch(S3_MOD + ".list_stale_mpus", return_value=[]), \
                patch(S3_MOD + ".list_objects", return_value=[]):
            self.env["secure.transfer"]._cron_gc_drafts()
        self.assertEqual(t.state, "draft")
        self.assertNotEqual(t.file_ids.state, "purged")
        self.assertNotIn(t.file_ids.s3_key, self._keys_deleted(delete))

    def test_gc_drafts_sweeps_orphan_mpus_scoped_to_tenant_prefix(self):
        """All tenants may share one bucket. An unscoped ListMultipartUploads
        would abort another tenant's in-flight uploads — data loss in a
        database this code has no business touching."""
        orphan = {"key": "whatever/abandoned.zip",
                  "upload_id": "upl-orphan-" + uuid.uuid4().hex,
                  "initiated": None}
        with patch(S3_MOD + ".delete_keys", return_value=[]), \
                patch(S3_MOD + ".mpu_abort", return_value=True) as abort, \
                patch(S3_MOD + ".list_stale_mpus", return_value=[orphan]) as lsm, \
                patch(S3_MOD + ".list_objects", return_value=[]):
            self.env["secure.transfer"]._cron_gc_drafts()

        self.assertEqual(lsm.call_args.args[1], s3.key_prefix(self.env) + "/",
                         "the MPU sweep is not scoped to this tenant's prefix")
        self.assertEqual(lsm.call_args.args[2], MPU_SWEEP_GRACE_HOURS)
        self.assertEqual(MPU_SWEEP_GRACE_HOURS, 48)
        aborted = {(c.args[1], c.args[2]) for c in abort.call_args_list}
        self.assertIn((orphan["key"], orphan["upload_id"]), aborted)

    def test_gc_drafts_never_aborts_a_claimed_mpu(self):
        """The DB row is the only thing that says « this upload is alive ».
        Aborting an MPU a live file line still claims destroys the parts of an
        upload in progress — the sender's browser then fails at Complete with
        no way back."""
        t = self._create()
        f = t._register_file("gros.zip", 100 * MB)
        claimed = "upl-claimed-" + uuid.uuid4().hex
        f.sudo().write({"s3_upload_id": claimed})
        stale = [{"key": f.s3_key, "upload_id": claimed, "initiated": None}]
        with patch(S3_MOD + ".delete_keys", return_value=[]), \
                patch(S3_MOD + ".mpu_abort", return_value=True) as abort, \
                patch(S3_MOD + ".list_stale_mpus", return_value=stale), \
                patch(S3_MOD + ".list_objects", return_value=[]):
            self.env["secure.transfer"]._cron_gc_drafts()
        aborted = {c.args[2] for c in abort.call_args_list}
        self.assertNotIn(claimed, aborted)
        self.assertEqual(f.s3_upload_id, claimed)

    def test_gc_drafts_sweeps_orphan_objects_and_spares_probes(self):
        """Objects with no DB row are pure cost. probes/ is the bucket-setup
        action's own scratch space: sweeping it would make the connectivity
        self-test fight the GC every hour."""
        prefix = s3.key_prefix(self.env) + "/"
        orphan = prefix + "abandoned/" + uuid.uuid4().hex + ".bin"
        probe = prefix + "probes/" + uuid.uuid4().hex
        objects = [{"key": orphan, "last_modified": None, "size": 12},
                   {"key": probe, "last_modified": None, "size": 1}]
        with patch(S3_MOD + ".delete_keys", return_value=[]) as delete, \
                patch(S3_MOD + ".mpu_abort", return_value=True), \
                patch(S3_MOD + ".list_stale_mpus", return_value=[]), \
                patch(S3_MOD + ".list_objects", return_value=objects) as lo:
            self.env["secure.transfer"]._cron_gc_drafts()

        self.assertEqual(lo.call_args.args[1], prefix,
                         "the object sweep is not scoped to this tenant")
        self.assertEqual(lo.call_args.args[2], MPU_SWEEP_GRACE_HOURS)
        deleted = self._keys_deleted(delete)
        self.assertIn(orphan, deleted)
        self.assertNotIn(probe, deleted)

    def test_gc_drafts_never_deletes_objects_of_live_file_rows(self):
        """This is the module's most dangerous false positive: a key whose row
        is pending/uploading/uploaded/verified is a client upload in flight or
        already delivered. Deleting it destroys a file the recipient is about
        to download, with nothing to restore it from."""
        live_keys = {}
        for state in ("pending", "uploading", "uploaded", "verified"):
            t = self._create()
            f = t._register_file("doc-%s.pdf" % state, 4096)
            f.sudo().write({"state": state})
            live_keys[state] = f.s3_key
        objects = [{"key": k, "last_modified": None, "size": 4096}
                   for k in live_keys.values()]
        with patch(S3_MOD + ".delete_keys", return_value=[]) as delete, \
                patch(S3_MOD + ".mpu_abort", return_value=True), \
                patch(S3_MOD + ".list_stale_mpus", return_value=[]), \
                patch(S3_MOD + ".list_objects", return_value=objects):
            self.env["secure.transfer"]._cron_gc_drafts()
        deleted = self._keys_deleted(delete)
        for state, key in live_keys.items():
            self.assertNotIn(
                key, deleted,
                "the GC deleted the object of a %s file row" % state)

    def test_gc_drafts_survives_unusable_s3(self):
        """This job runs hourly. Letting a misconfiguration or an outage raise
        would fill the log with tracebacks and eventually get the cron
        deactivated by Odoo — drafts would then accumulate forever."""
        for boom in (UserError("Stockage S3 non configuré"), _endpoint_error()):
            with self.subTest(error=type(boom).__name__):
                with patch(S3_MOD + ".delete_keys", return_value=[]), \
                        patch(S3_MOD + ".mpu_abort", return_value=True), \
                        patch(S3_MOD + ".list_stale_mpus", side_effect=boom), \
                        patch(S3_MOD + ".list_objects", side_effect=boom):
                    self.env["secure.transfer"]._cron_gc_drafts()  # no raise

    # =================================================================
    #  _cron_gc_logs  — the only destructive path in the module
    # =================================================================
    def test_gc_logs_deletes_past_retention_with_its_trail(self):
        """Keeping access logs forever is itself a Loi 25 problem: the trail
        names senders and recipients. Past the retention period the record and
        its journal must actually go, cascade included."""
        t = self._deleted_transfer()
        t.sudo().write({"purged_at": fields.Datetime.now() - timedelta(
            days=LOG_RETENTION_DAYS + 1)})
        log_ids = t.access_log_ids.ids
        self.assertTrue(log_ids, "fixture has no trail to cascade")
        transfer_id = t.id

        self.env["secure.transfer"]._cron_gc_logs()

        self.assertFalse(t.exists())
        self.assertFalse(
            self.env["secure.transfer.access.log"].sudo().search(
                [("id", "in", log_ids)]),
            "the access-log entries survived their parent transfer",
        )
        self.assertFalse(
            self.env["secure.transfer"].search([("id", "=", transfer_id)]))

    def test_gc_logs_preserves_a_transfer_inside_retention(self):
        """Loi 25 evidence. Deleting a transfer one day early destroys the
        only proof of who accessed what and when — unrecoverable, and exactly
        what a client would ask for during an incident."""
        t = self._deleted_transfer()
        t.sudo().write({"purged_at": fields.Datetime.now() - timedelta(days=30)})
        log_ids = t.access_log_ids.ids

        self.env["secure.transfer"]._cron_gc_logs()

        self.assertTrue(t.exists(), "a transfer INSIDE retention was destroyed")
        self.assertEqual(t.state, "deleted")
        self.assertEqual(
            len(self.env["secure.transfer.access.log"].sudo().search(
                [("id", "in", log_ids)])),
            len(log_ids),
            "access-log entries were destroyed inside the retention period",
        )

    def test_gc_logs_uses_purged_at_when_set_write_date_otherwise(self):
        """The OR branch decides what « old » means. Reading write_date on a
        record that HAS a purged_at would delete a recently purged transfer
        whose row simply has not been touched in a year — and reading only
        purged_at would leave cancelled drafts (purged_at NULL) forever."""
        old = fields.Datetime.now() - timedelta(days=LOG_RETENTION_DAYS + 5)

        # purged_at recent, write_date ancient -> purged_at wins, KEEP
        recent_purge = self._deleted_transfer()
        recent_purge.sudo().write({"purged_at": fields.Datetime.now()})
        self._backdate(recent_purge, write_date=old)

        # purged_at NULL, write_date ancient -> falls back to write_date, GO
        no_purge = self._create()
        no_purge.sudo().write({"state": "cancelled", "purged_at": False})
        self._backdate(no_purge, write_date=old)
        no_purge_id = no_purge.id

        self.env["secure.transfer"]._cron_gc_logs()

        self.assertTrue(
            recent_purge.exists(),
            "purged_at must take precedence over a stale write_date")
        self.assertFalse(
            self.env["secure.transfer"].search([("id", "=", no_purge_id)]),
            "a cancelled transfer with no purged_at was never collected")

    def test_gc_logs_never_touches_a_live_transfer(self):
        """A long-lived active transfer (30-day retention, untouched row) is
        still serving downloads. Collecting it on age alone would delete a
        live share link and its evidence in one shot."""
        t = self._active_transfer()
        self._backdate(t, create_date=fields.Datetime.now() - timedelta(days=900),
                       write_date=fields.Datetime.now() - timedelta(days=900))
        self.env["secure.transfer"]._cron_gc_logs()
        self.assertTrue(t.exists(), "the hard GC collected an ACTIVE transfer")
        self.assertEqual(t.state, "active")

    # =================================================================
    #  cron definitions
    # =================================================================
    CRONS = {
        "bf_securetransfer.cron_st_purge": "_cron_purge_expired",
        "bf_securetransfer.cron_st_gc_drafts": "_cron_gc_drafts",
        "bf_securetransfer.cron_st_gc_logs": "_cron_gc_logs",
    }

    def test_cron_records_call_real_methods(self):
        """A renamed method leaves the XML pointing at nothing: the job fails
        at every trigger and Odoo eventually deactivates it. Nobody notices
        until the bucket bill arrives or a client asks why files past their
        retention are still downloadable."""
        model = self.env["secure.transfer"]
        for xmlid, method in self.CRONS.items():
            with self.subTest(cron=xmlid):
                cron = self.env.ref(xmlid, raise_if_not_found=False)
                self.assertTrue(cron, "%s does not exist" % xmlid)
                self.assertEqual(cron.model_id.model, "secure.transfer")
                self.assertEqual(cron.state, "code")
                called = re.findall(r"model\.(_\w+)\(", cron.code or "")
                self.assertEqual(called, [method], "unexpected cron body")
                self.assertTrue(
                    hasattr(model, called[0]),
                    "%s calls %s(), which does not exist on the model"
                    % (xmlid, called[0]),
                )

    def test_cron_records_ship_enabled(self):
        """Shipping a cron with active=False (or dropping the field, which in
        ir.cron still defaults to True but has been flipped by hand before)
        means the module installs on a client tenant and never purges
        anything — files live past their retention with no visible symptom.

        The assertion is on the DATA FILE, not on the loaded record: the
        records carry noupdate=1, and an operator legitimately owns the live
        flag afterwards (a refreshed staging database has every cron in the
        instance switched off on purpose — 132 of them here)."""
        with file_open(
            "bf_securetransfer/data/secure_transfer_cron.xml", "r"
        ) as fh:
            root = ElementTree.fromstring(fh.read())
        declared = {}
        for rec in root.iter("record"):
            if rec.get("model") != "ir.cron":
                continue
            active = rec.find("field[@name='active']")
            declared[rec.get("id")] = (
                active is None or (active.get("eval") or "").strip() == "True")
        for xmlid in self.CRONS:
            short = xmlid.split(".", 1)[1]
            with self.subTest(cron=short):
                self.assertIn(short, declared, "%s left the data file" % short)
                self.assertTrue(
                    declared[short], "%s ships DISABLED" % short)
