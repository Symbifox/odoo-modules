"""Multipart upload cycle: the path every multi-GB file takes.

Covers ``secure.transfer.file`` (``mpu_initiate`` / ``mpu_sign`` /
``mpu_status`` / ``mpu_complete`` / ``mpu_abort`` / ``_s3_delete``) and the
JSON routes that drive them from the browser.

Every S3 touchpoint is patched on
``odoo.addons.bf_securetransfer.models.s3`` (the single boto3 gateway): no
network, and the suite runs on a build without boto3. Assertions are made on
the CALLS to those mocks (count + arguments), not only on the final state —
an orphaned multipart upload costs storage forever and leaves no trace in the
record.

The route tests call the controller methods directly with a stubbed
``request`` (``@route`` on a type="json" endpoint returns the raw result, so a
direct call is faithful) — that keeps the whole file in one TransactionCase.
"""
import contextlib
import math

from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

from ..controllers.upload_api import SecureTransferUploadApi, _file_or_error

S3_MOD = "odoo.addons.bf_securetransfer.models.s3"
API_MOD = "odoo.addons.bf_securetransfer.controllers.upload_api"
MAIN_MOD = "odoo.addons.bf_securetransfer.controllers.main"
MB = 1024 * 1024

# 100 MB is over the 64 MB multipart threshold and, at the default 16 MB part
# size, gives 7 parts (6 full + a 4 MB tail) — enough to exercise clamping.
BIG = 100 * MB
PART = 16 * MB
UPLOAD_ID = "upl-test-0001"


class _FakeHttpRequest:
    def __init__(self, ip):
        self.remote_addr = ip
        self.headers = {"User-Agent": "test-suite/1.0"}


class _FakeRequest:
    """Minimum surface the upload API touches: ``env`` and ``httprequest``."""

    def __init__(self, env, ip):
        self.env = env
        self.httprequest = _FakeHttpRequest(ip)


@tagged("post_install", "-at_install")
class TestMultipartUpload(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.brand = cls.env.ref("bf_securetransfer.brand_default")
        icp = cls.env["ir.config_parameter"].sudo()
        # Roomy daily quotas so the suite never trips anti-abuse counters.
        icp.set_param("bf_securetransfer.quota_daily_transfers_per_ip", "500")
        icp.set_param("bf_securetransfer.quota_daily_transfers_per_sender", "500")
        icp.set_param("bf_securetransfer.quota_daily_bytes_per_ip_mb", "1000000")

    # ------------------------------------------------------------------ fixtures
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
            self.brand, vals, "203.0.113.55", "test-suite/1.0", "fr_CA",
        )

    def _big_file(self, transfer=None, name="gros.zip", size=BIG):
        t = transfer or self._transfer()
        f = t._register_file(name, size)
        self.assertEqual(f.upload_mode, "multipart",
                         "fixture is supposed to be over the MPU threshold")
        return f

    def _initiated(self, size=BIG, upload_id=UPLOAD_ID):
        """A multipart file with its plan already opened on (mocked) S3."""
        f = self._big_file(size=size)
        with patch(S3_MOD + ".mpu_initiate", return_value=upload_id):
            f.mpu_initiate()
        return f

    def _set_param(self, key, value):
        icp = self.env["ir.config_parameter"].sudo()
        previous = icp.get_param("bf_securetransfer." + key)
        icp.set_param("bf_securetransfer." + key, value)
        self.addCleanup(
            icp.set_param, "bf_securetransfer." + key, previous or "")

    @contextlib.contextmanager
    def _as_request(self):
        """Both controller modules bound ``request`` at import time, so each
        namespace needs its own patch."""
        fake = _FakeRequest(self.env, "203.0.113.55")
        with patch(API_MOD + ".request", fake), patch(MAIN_MOD + ".request", fake):
            yield

    # ================================================================== initiate
    def test_mpu_initiate_builds_the_plan(self):
        """A wrong part count means the browser stops uploading before the end
        (short object) or signs parts S3 will never accept."""
        f = self._big_file()
        with patch(S3_MOD + ".mpu_initiate", return_value=UPLOAD_ID) as init:
            plan = f.mpu_initiate()
        init.assert_called_once()
        self.assertEqual(init.call_args.args[1], f.s3_key)
        self.assertEqual(plan["upload_id"], UPLOAD_ID)
        self.assertEqual(plan["part_size"], PART)
        self.assertEqual(plan["parts_total"], math.ceil(BIG / PART))
        self.assertEqual(f.parts_total, 7)
        self.assertEqual(f.state, "uploading")
        self.assertEqual(f.s3_upload_id, UPLOAD_ID)

    def test_mpu_initiate_is_idempotent(self):
        """A resumed session that re-initiates would open a SECOND multipart
        upload on S3: the first one is then unreferenced, invisible to the
        purge cron, and billed as stored bytes until the lifecycle net (if the
        provider even honours it) catches it."""
        f = self._big_file()
        with patch(S3_MOD + ".mpu_initiate", return_value=UPLOAD_ID) as init:
            first = f.mpu_initiate()
            second = f.mpu_initiate()
        self.assertEqual(init.call_count, 1, "a second MPU was opened on S3")
        self.assertEqual(first, second)
        self.assertEqual(f.s3_upload_id, UPLOAD_ID)

    def test_mpu_initiate_refused_on_a_simple_file(self):
        """Opening an MPU on a file the browser will PUT in one shot leaves a
        multipart upload nobody ever completes or aborts."""
        t = self._transfer()
        f = t._register_file("petit.pdf", 5 * MB)
        self.assertEqual(f.upload_mode, "simple")
        with patch(S3_MOD + ".mpu_initiate") as init:
            with self.assertRaises(UserError):
                f.mpu_initiate()
        init.assert_not_called()

    def test_presign_simple_refused_on_a_multipart_file(self):
        """A simple PUT signed for a multi-GB body: the endpoint refuses it (or
        worse, accepts a truncated one) — the mode must be honoured."""
        f = self._big_file()
        with patch(S3_MOD + ".presign_put") as presign:
            with self.assertRaises(UserError):
                f.presign_simple()
        presign.assert_not_called()

    # =================================================================== guards
    def test_upload_operations_die_with_the_draft(self):
        """Once the transfer is sent, the upload capability is gone: a leaked
        upload_token must not let anyone add or replace bytes behind a link
        the recipient already trusts."""
        t = self._transfer()
        simple = t._register_file("petit.pdf", 5 * MB)
        big = self._big_file(transfer=t)
        with patch(S3_MOD + ".mpu_initiate", return_value=UPLOAD_ID):
            big.mpu_initiate()
        t.state = "active"
        with patch(S3_MOD + ".presign_put") as presign, \
                patch(S3_MOD + ".mpu_initiate") as init, \
                patch(S3_MOD + ".mpu_sign_parts") as sign, \
                patch(S3_MOD + ".mpu_list_parts") as lst, \
                patch(S3_MOD + ".mpu_complete") as comp:
            with self.assertRaises(UserError):
                simple.presign_simple()
            with self.assertRaises(UserError):
                big.mpu_initiate()
            with self.assertRaises(UserError):
                big.mpu_sign([1])
            with self.assertRaises(UserError):
                big.mpu_status()
            with self.assertRaises(UserError):
                big.mpu_complete()
            for mock in (presign, init, sign, lst, comp):
                mock.assert_not_called()

    def test_abort_stays_legal_after_the_draft_closed(self):
        """Deliberate asymmetry: ``mpu_abort`` does NOT call
        ``_check_uploadable``. It is the cleanup path (``api_remove``,
        ``_s3_delete``, the draft GC cron) — gating it would strand live
        multipart uploads on the bucket forever."""
        f = self._initiated()
        f.transfer_id.state = "active"
        with patch(S3_MOD + ".mpu_abort", return_value=True) as abort:
            self.assertTrue(f.mpu_abort())
        abort.assert_called_once()
        self.assertFalse(f.s3_upload_id)

    # ===================================================================== sign
    def test_mpu_sign_returns_urls_for_valid_numbers(self):
        f = self._initiated()
        with patch(
            S3_MOD + ".mpu_sign_parts",
            side_effect=lambda env, key, uid, nums: {n: "https://s3/%d" % n
                                                     for n in nums},
        ) as sign:
            urls = f.mpu_sign([3, 1, 2])
        sign.assert_called_once()
        env_, key, uid, nums = sign.call_args.args
        self.assertEqual(key, f.s3_key)
        self.assertEqual(uid, UPLOAD_ID)
        self.assertEqual(nums, [1, 2, 3], "part numbers must be sorted+deduped")
        self.assertEqual(sorted(urls), [1, 2, 3])

    def test_mpu_sign_clamps_out_of_range_numbers(self):
        """Signing part 0 or part 10 000 on a 7-part upload hands the browser
        URLs S3 rejects, and turns the endpoint into a free presign oracle for
        keys outside the plan."""
        f = self._initiated()
        with patch(
            S3_MOD + ".mpu_sign_parts",
            side_effect=lambda env, key, uid, nums: {n: "u" for n in nums},
        ) as sign:
            f.mpu_sign([0, -5, 1, 7, 8, 99999])
        self.assertEqual(sign.call_args.args[3], [1, 7])

    def test_mpu_sign_truncates_to_the_batch_ceiling(self):
        """No ceiling = presign-farming: one call yields as many long-lived
        upload URLs as the caller asks for."""
        self._set_param("mpu_sign_batch_max", "3")
        f = self._initiated()
        with patch(
            S3_MOD + ".mpu_sign_parts",
            side_effect=lambda env, key, uid, nums: {n: "u" for n in nums},
        ) as sign:
            urls = f.mpu_sign([1, 2, 3, 4, 5, 6, 7])
        self.assertEqual(sign.call_args.args[3], [1, 2, 3])
        self.assertEqual(len(urls), 3)

    def test_mpu_sign_without_an_upload_id(self):
        """Signing parts for an upload that was never opened produces URLs
        bound to a dead UploadId — every PUT 404s and the user sees nothing."""
        f = self._big_file()
        self.assertFalse(f.s3_upload_id)
        with patch(S3_MOD + ".mpu_sign_parts") as sign:
            with self.assertRaises(UserError):
                f.mpu_sign([1])
        sign.assert_not_called()

    def test_mpu_sign_rejects_unconvertible_numbers(self):
        """Garbage in the part list must fail closed, not crash with a raw
        TypeError serialized to the public JSON layer."""
        f = self._initiated()
        with patch(S3_MOD + ".mpu_sign_parts") as sign:
            for bad in (["abc"], [None], [{"n": 1}], [[1]]):
                with self.assertRaises(UserError, msg=repr(bad)):
                    f.mpu_sign(bad)
        sign.assert_not_called()

    def test_mpu_sign_rejects_an_empty_selection(self):
        f = self._initiated()
        with patch(S3_MOD + ".mpu_sign_parts") as sign:
            with self.assertRaises(UserError):
                f.mpu_sign([])                 # nothing asked
            with self.assertRaises(UserError):
                f.mpu_sign([0, 8, 12345])      # nothing survives the clamp
        sign.assert_not_called()

    # =================================================================== status
    def test_mpu_status_lists_and_caches_the_parts(self):
        """Resume depends on this: ListParts is the source of truth, the
        manifest is only a cache. A stale/absent manifest makes the browser
        re-upload gigabytes it already sent."""
        f = self._initiated()
        parts = [{"n": 1, "etag": "e1", "size": PART},
                 {"n": 2, "etag": "e2", "size": PART}]
        with patch(S3_MOD + ".mpu_list_parts", return_value=parts) as lst:
            res = f.mpu_status()
        lst.assert_called_once()
        self.assertEqual(lst.call_args.args[1], f.s3_key)
        self.assertEqual(lst.call_args.args[2], UPLOAD_ID)
        self.assertEqual(res, {"parts": parts})
        self.assertEqual(f.parts_manifest, parts)

    def test_mpu_status_without_an_upload_id_is_empty(self):
        """Nothing started yet: answer "no parts" instead of calling ListParts
        with a null UploadId (a 400 the page would show as a hard error)."""
        f = self._big_file()
        with patch(S3_MOD + ".mpu_list_parts") as lst:
            self.assertEqual(f.mpu_status(), {"parts": []})
        lst.assert_not_called()

    # ================================================================= complete
    @staticmethod
    def _full_parts(size=BIG, part=PART):
        """Part list whose sizes add up EXACTLY to `size`."""
        out, n, left = [], 1, int(size)
        while left > 0:
            chunk = min(part, left)
            out.append({"n": n, "etag": "e%d" % n, "size": chunk})
            left -= chunk
            n += 1
        return out

    def test_mpu_complete_assembles_and_pins_the_etag(self):
        f = self._initiated()
        parts = self._full_parts()
        self.assertEqual(sum(p["size"] for p in parts), int(f.size))
        with patch(S3_MOD + ".mpu_list_parts", return_value=parts), \
                patch(S3_MOD + ".mpu_complete", return_value="final") as comp, \
                patch(S3_MOD + ".head_object",
                      return_value={"size": int(BIG), "etag": "final"}) as head, \
                patch(S3_MOD + ".mpu_abort") as abort:
            self.assertTrue(f.mpu_complete())
        comp.assert_called_once()
        self.assertEqual(comp.call_args.args[1:], (f.s3_key, UPLOAD_ID))
        head.assert_called_once()
        abort.assert_not_called()
        self.assertEqual(f.state, "uploaded")
        self.assertEqual(f.size_confirmed, BIG)
        self.assertEqual(f.etag, "final")
        self.assertFalse(f.s3_upload_id, "the UploadId is dead after Complete")
        self.assertEqual(f.parts_manifest, parts)

    def test_mpu_complete_aborts_on_a_size_mismatch(self):
        """Stored bytes ≠ declared bytes means a part is missing or the
        declared size was tampered with. Completing anyway would publish a
        truncated file under a link the sender believes is good — and leave
        the MPU open. NB: plain try/except, not assertRaises: Odoo's
        assertRaises rolls back to a savepoint and would discard the very
        writes this test asserts on."""
        f = self._initiated()
        short = self._full_parts()[:-1]        # one part short
        with patch(S3_MOD + ".mpu_list_parts", return_value=short), \
                patch(S3_MOD + ".mpu_complete") as comp, \
                patch(S3_MOD + ".mpu_abort", return_value=True) as abort:
            try:
                f.mpu_complete()
                self.fail("an incomplete upload must be refused")
            except UserError as exc:
                self.assertIn("incomplet", str(exc).lower())
        comp.assert_not_called()
        abort.assert_called_once()
        self.assertEqual(abort.call_args.args[1:], (f.s3_key, UPLOAD_ID))
        self.assertEqual(f.state, "error")
        self.assertFalse(f.s3_upload_id)
        self.assertFalse(f.parts_manifest)

    def test_mpu_complete_aborts_when_no_part_was_stored(self):
        """The empty case must not fall through to CompleteMultipartUpload
        (which fails server-side and leaves the MPU dangling)."""
        f = self._initiated()
        with patch(S3_MOD + ".mpu_list_parts", return_value=[]), \
                patch(S3_MOD + ".mpu_complete") as comp, \
                patch(S3_MOD + ".mpu_abort", return_value=True) as abort:
            try:
                f.mpu_complete()
                self.fail("an empty upload must be refused")
            except UserError:
                pass
        comp.assert_not_called()
        abort.assert_called_once()
        self.assertEqual(f.state, "error")

    def test_mpu_complete_refuses_an_inconsistent_head(self):
        """Last line of defence: the assembled object is re-measured. Skipping
        it would pin an ETag on an object whose real size nobody checked."""
        f = self._initiated()
        parts = self._full_parts()
        with patch(S3_MOD + ".mpu_list_parts", return_value=parts), \
                patch(S3_MOD + ".mpu_complete", return_value="final") as comp, \
                patch(S3_MOD + ".head_object",
                      return_value={"size": 42, "etag": "final"}), \
                patch(S3_MOD + ".mpu_abort") as abort:
            try:
                f.mpu_complete()
                self.fail("a short assembled object must be refused")
            except UserError as exc:
                self.assertIn("vérification", str(exc).lower())
        comp.assert_called_once()
        abort.assert_not_called()   # the UploadId is already dead
        self.assertEqual(f.state, "error")
        self.assertFalse(f.s3_upload_id)
        self.assertFalse(f.etag, "no ETag may be pinned on a failed assembly")

    def test_mpu_complete_refuses_a_missing_object(self):
        """HEAD returning None (object absent) must be treated like a size
        mismatch, not crash on ``head["size"]``."""
        f = self._initiated()
        with patch(S3_MOD + ".mpu_list_parts", return_value=self._full_parts()), \
                patch(S3_MOD + ".mpu_complete", return_value="final"), \
                patch(S3_MOD + ".head_object", return_value=None):
            try:
                f.mpu_complete()
                self.fail("a missing assembled object must be refused")
            except UserError:
                pass
        self.assertEqual(f.state, "error")

    def test_mpu_complete_without_an_upload_id(self):
        f = self._big_file()
        with patch(S3_MOD + ".mpu_list_parts") as lst:
            with self.assertRaises(UserError):
                f.mpu_complete()
        lst.assert_not_called()

    # ==================================================================== abort
    def test_mpu_abort_resets_the_plan(self):
        """After an abort the file must be re-uploadable from scratch: a
        surviving UploadId would make the next sign/complete talk to an MPU
        S3 has already thrown away."""
        f = self._initiated()
        f.write({"parts_manifest": [{"n": 1, "etag": "e1", "size": PART}]})
        with patch(S3_MOD + ".mpu_abort", return_value=True) as abort:
            self.assertTrue(f.mpu_abort())
        abort.assert_called_once()
        self.assertEqual(abort.call_args.args[1:], (f.s3_key, UPLOAD_ID))
        self.assertFalse(f.s3_upload_id)
        self.assertFalse(f.parts_manifest)
        self.assertEqual(f.state, "pending")

    def test_mpu_abort_is_a_noop_without_an_upload_id(self):
        """``api_remove`` calls abort on every file it deletes, multipart or
        not — an unconditional AbortMultipartUpload would 400 on each one."""
        f = self._big_file()
        with patch(S3_MOD + ".mpu_abort") as abort:
            self.assertTrue(f.mpu_abort())
        abort.assert_not_called()
        self.assertEqual(f.state, "pending")

    # ================================================================ _s3_delete
    def test_s3_delete_aborts_then_removes_the_object(self):
        f = self._initiated()
        with patch(S3_MOD + ".mpu_abort", return_value=True) as abort, \
                patch(S3_MOD + ".delete_keys", return_value=[]) as delete:
            self.assertTrue(f._s3_delete())
        abort.assert_called_once()
        self.assertEqual(abort.call_args.args[1:], (f.s3_key, UPLOAD_ID))
        delete.assert_called_once()
        self.assertEqual(delete.call_args.args[1], [f.s3_key])
        self.assertFalse(f.s3_upload_id)
        self.assertEqual(f.state, "purged")

    def test_s3_delete_keeps_the_trace_when_the_abort_fails(self):
        """Marking the file "purged" while its multipart upload is still alive
        on the bucket loses the only pointer we have to it: nothing would ever
        abort it again and it would be billed as stored bytes."""
        f = self._initiated()
        with patch(S3_MOD + ".mpu_abort", side_effect=Exception("boom")) as abort, \
                patch(S3_MOD + ".delete_keys", return_value=[]) as delete:
            self.assertFalse(f._s3_delete())
        abort.assert_called_once()
        delete.assert_called_once()             # best effort on the object
        self.assertNotEqual(f.state, "purged")
        self.assertEqual(f.s3_upload_id, UPLOAD_ID,
                         "the live UploadId must survive a failed abort")

    def test_s3_delete_reports_a_failed_object_delete(self):
        f = self._big_file()
        with patch(S3_MOD + ".delete_keys", return_value=[f.s3_key]):
            self.assertFalse(f._s3_delete())
        self.assertNotEqual(f.state, "purged")

    # ============================================================ routes: sign
    def _route_sign(self, transfer, rec_file, part_numbers):
        with self._as_request():
            return SecureTransferUploadApi().api_mpu_sign(
                transfer.sudo().upload_token,
                file_id=rec_file.id,
                part_numbers=part_numbers,
            )

    def test_route_sign_rejects_empty_and_non_list_payloads(self):
        """The part list comes straight from the browser. A string or a dict
        reaching the clamp loop iterates characters/keys and signs nonsense."""
        f = self._initiated()
        t = f.transfer_id
        for bad in ([], "1,2,3", {"1": 1}, None, 5):
            res = self._route_sign(t, f, bad)
            self.assertEqual(res.get("error"), "invalid_parts", repr(bad))

    def test_route_sign_rejects_an_oversized_batch_before_conversion(self):
        """The raw-length check must run BEFORE dedup/int(): a batch of
        duplicates collapses to one number, so a post-dedup ceiling alone would
        happily accept an arbitrarily long body and pay the int() parsing cost
        (~O(digits²)) on every element — a cheap-body CPU DoS."""
        self._set_param("mpu_sign_batch_max", "5")
        f = self._initiated()
        t = f.transfer_id
        with patch(S3_MOD + ".mpu_sign_parts") as sign:
            res = self._route_sign(t, f, [1] * 6)
        self.assertEqual(res.get("error"), "invalid_parts")
        sign.assert_not_called()
        # Control: the same duplicates UNDER the ceiling still go through, so
        # the rejection above really is the raw-length guard and not dedup.
        with patch(
            S3_MOD + ".mpu_sign_parts",
            side_effect=lambda env, key, uid, nums: {n: "https://s3/%d" % n
                                                     for n in nums},
        ):
            ok = self._route_sign(t, f, [1, 1])
        self.assertEqual(ok, {"urls": {"1": "https://s3/1"}})

    def test_route_sign_rejects_booleans_and_bad_strings(self):
        """``True`` is an ``int`` subclass in Python: without an explicit
        check, ``[True]`` signs part 1 for a caller that sent no number at all.
        Long digit strings are the other half of the DoS surface."""
        f = self._initiated()
        t = f.transfer_id
        with patch(S3_MOD + ".mpu_sign_parts") as sign:
            for bad in ([True], [False], [1, True], ["abc"], ["12345678"],
                        ["9" * 100000], ["1.0"], [""], [1.5], [None]):
                res = self._route_sign(t, f, bad)
                self.assertEqual(res.get("error"), "invalid_parts", repr(bad))
        sign.assert_not_called()

    def test_route_sign_rejects_numbers_outside_the_plan(self):
        f = self._initiated()          # parts_total == 7
        t = f.transfer_id
        with patch(S3_MOD + ".mpu_sign_parts") as sign:
            for bad in ([0], [8], [1, 8], [-1]):
                res = self._route_sign(t, f, bad)
                self.assertEqual(res.get("error"), "invalid_parts", repr(bad))
        sign.assert_not_called()

    # ====================================================== routes: file lookup
    def test_route_rejects_a_file_from_another_transfer(self):
        """``file_id`` is caller-controlled. Without the ownership filter, an
        upload_token for transfer A would presign/complete/delete parts of
        transfer B — a cross-tenant write with a valid token."""
        mine = self._initiated()
        other = self._initiated()
        self.assertNotEqual(mine.transfer_id, other.transfer_id)
        rec, error = _file_or_error(mine.transfer_id, other.id)
        self.assertIsNone(rec)
        self.assertEqual(error.get("error"), "invalid_file")
        # and its own file still resolves
        rec, error = _file_or_error(mine.transfer_id, mine.id)
        self.assertEqual(rec, mine)
        self.assertIsNone(error)

    def test_route_rejects_an_unparseable_file_id(self):
        f = self._initiated()
        for bad in ("abc", None, {"id": 1}, [1]):
            rec, error = _file_or_error(f.transfer_id, bad)
            self.assertIsNone(rec, repr(bad))
            self.assertEqual(error.get("error"), "invalid_file", repr(bad))

    def test_route_status_reaches_the_model(self):
        """End-to-end wiring check on the resume endpoint: token → file →
        ListParts, with the manifest cached on the way out."""
        f = self._initiated()
        parts = [{"n": 1, "etag": "e1", "size": PART}]
        with self._as_request(), \
                patch(S3_MOD + ".mpu_list_parts", return_value=parts) as lst:
            res = SecureTransferUploadApi().api_mpu_status(
                f.transfer_id.sudo().upload_token, file_id=f.id)
        lst.assert_called_once()
        self.assertEqual(res, {"parts": parts})
        self.assertEqual(f.parts_manifest, parts)
