"""The boto3 gateway (``models/s3.py``) — the single file that talks to the
S3-compatible endpoint.

Nothing here touches the network: ``s3.client`` is replaced by a MagicMock,
``s3._client_error`` by a local exception class and ``s3._http`` by a stub that
raises. The suite therefore runs on an image WITHOUT boto3 installed, and a
regression can never turn into a live call against IDrive E2.

Credentials are forced through the environment (never the database) with
``patch.dict(os.environ, ...)`` plus an empty ``odoo_config``, mirroring the
production contract documented at the top of ``s3.py``.
"""
import os
import sys
from datetime import datetime, timedelta, timezone

from unittest.mock import MagicMock, patch

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

from odoo.addons.bf_securetransfer.models import s3

from .common import LanguesActives

# ---------------------------------------------------------------- ClientError
# Real botocore class when the image has it (so the isinstance/except paths are
# exercised exactly as in production), a structural double otherwise.
try:  # pragma: no cover - depends on the image
    from botocore.exceptions import ClientError as _BotoClientError
except ImportError:  # pragma: no cover
    _BotoClientError = None


class _FakeClientError(Exception):
    """Structural stand-in for botocore's ClientError (same ``.response``)."""

    def __init__(self, error_response, operation_name="Op"):
        self.response = error_response
        self.operation_name = operation_name
        super().__init__(str(error_response))


CLIENT_ERROR = _BotoClientError or _FakeClientError


def cerr(code, operation="Op", status=None):
    """Build a ClientError carrying an S3 error code (and optional HTTP code)."""
    resp = {"Error": {"Code": code, "Message": code}}
    if status is not None:
        resp["ResponseMetadata"] = {"HTTPStatusCode": status}
    return CLIENT_ERROR(resp, operation)


BUCKET = "bf-test-bucket"


@tagged("post_install", "-at_install")
class TestS3Gateway(LanguesActives, TransactionCase):

    def setUp(self):
        super().setUp()
        self.icp = self.env["ir.config_parameter"].sudo()
        self.icp.set_param("bf_securetransfer.s3_endpoint_url",
                           "https://s3.example.test")
        self.icp.set_param("bf_securetransfer.s3_bucket", BUCKET)
        self.icp.set_param("bf_securetransfer.s3_region", "ca-east-1")
        self.icp.set_param("bf_securetransfer.s3_path_style", "1")
        self.icp.set_param("bf_securetransfer.s3_key_prefix", "transfers")
        # Credentials: environment only. odoo_config is emptied so a stray
        # odoo.conf entry on the runner cannot make a test pass by accident.
        self._start(patch.dict(os.environ, {
            "BF_SECURETRANSFER_S3_ACCESS_KEY": "ENV-ACCESS-KEY",
            "BF_SECURETRANSFER_S3_SECRET_KEY": "ENV-SECRET-KEY",
        }))
        self._start(patch.object(s3, "odoo_config", {}))
        # boto3 must never be imported by the code under test.
        self._start(patch.object(s3, "_client_error", lambda: CLIENT_ERROR))

    def _start(self, patcher):
        started = patcher.start()
        self.addCleanup(patcher.stop)
        return started

    def _mock_client(self):
        """Patch ``s3.client``; returns the fake boto3 client. The factory
        itself is kept on ``self.client_factory`` to assert on ``long_timeout``.
        """
        fake = MagicMock(name="s3_client")
        self.client_factory = self._start(
            patch.object(s3, "client", return_value=fake))
        return fake

    def _no_credentials(self):
        """Context manager: no S3 credential anywhere (env nor odoo.conf)."""
        clean = {k: v for k, v in os.environ.items()
                 if not k.startswith("BF_SECURETRANSFER_S3_")}
        return patch.dict(os.environ, clean, clear=True)

    def _isolate_brands(self):
        """Archive every existing brand so brand-derived computations
        (CORS origins, orphan expiry) are deterministic on any database."""
        self.env["secure.transfer.brand"].sudo().search([]).write(
            {"is_default": False, "active": False})

    # ------------------------------------------------------------- 1. params
    def test_params_requires_endpoint(self):
        """Without an endpoint, boto3 would silently target AWS instead of
        IDrive E2 — client files would leave the intended jurisdiction."""
        self.icp.set_param("bf_securetransfer.s3_endpoint_url", False)
        with self.assertRaises(UserError) as ctx:
            s3.params(self.env)
        self.assertIn("endpoint", str(ctx.exception).lower())

    def test_params_requires_bucket(self):
        """An empty bucket name turns every call into a malformed request at
        upload time instead of a clear configuration error."""
        self.icp.set_param("bf_securetransfer.s3_bucket", "   ")
        with self.assertRaises(UserError):
            s3.params(self.env)

    def test_params_credentials_never_come_from_the_database(self):
        """Keys in ir.config_parameter would be readable by any settings-level
        user and would land in every database backup."""
        # Plant credentials in the DB the way a well-meaning admin would.
        self.icp.set_param("bf_securetransfer.s3_access_key", "DB-ACCESS-KEY")
        self.icp.set_param("bf_securetransfer.s3_secret_key", "DB-SECRET-KEY")
        with self._no_credentials():
            with self.assertRaises(UserError) as ctx:
                s3.params(self.env)
        msg = str(ctx.exception)
        self.assertIn("odoo.conf", msg)
        self.assertNotIn("DB-ACCESS-KEY", msg)

    def test_params_reads_credentials_from_the_environment(self):
        """The env/odoo.conf path is the only supported one; if it broke, every
        tenant would fall back to 'no credentials' and all transfers would stop."""
        self.icp.set_param("bf_securetransfer.s3_access_key", "DB-ACCESS-KEY")
        p = s3.params(self.env)
        self.assertEqual(p["access_key"], "ENV-ACCESS-KEY")
        self.assertEqual(p["secret_key"], "ENV-SECRET-KEY")

    def test_params_odoo_conf_fallback(self):
        """Tenants configure keys in odoo.conf, not the environment: losing
        this fallback takes the whole module down on those instances."""
        with self._no_credentials(), patch.object(s3, "odoo_config", {
            "bf_securetransfer_s3_access_key": "CONF-AK",
            "bf_securetransfer_s3_secret_key": "CONF-SK",
        }):
            p = s3.params(self.env)
        self.assertEqual(p["access_key"], "CONF-AK")
        self.assertEqual(p["secret_key"], "CONF-SK")

    def test_params_defaults(self):
        """Presign TTLs and path-style addressing are the difference between a
        working upload page and 403s from the endpoint."""
        for key in ("s3_region", "s3_path_style", "presign_put_ttl",
                    "presign_part_ttl", "presign_get_ttl"):
            self.icp.set_param("bf_securetransfer." + key, False)
        p = s3.params(self.env)
        self.assertEqual(p["region"], "ca-east-1")
        self.assertTrue(p["path_style"])
        self.assertEqual(p["put_ttl"], 900)
        self.assertEqual(p["part_ttl"], 3600)
        self.assertEqual(p["get_ttl"], 300)
        self.assertEqual(p["bucket"], BUCKET)
        self.assertEqual(p["endpoint_url"], "https://s3.example.test")

    def test_params_path_style_can_be_disabled(self):
        """A tenant on a virtual-host-style endpoint needs this off; a stuck
        True would break every signature."""
        self.icp.set_param("bf_securetransfer.s3_path_style", "0")
        self.assertFalse(s3.params(self.env)["path_style"])

    # --------------------------------------------------------- 2. key_prefix
    def test_key_prefix_default(self):
        """The prefix isolates tenants sharing one bucket: no default means the
        purge sweep would run over the bucket root."""
        self.icp.set_param("bf_securetransfer.s3_key_prefix", False)
        self.assertEqual(s3.key_prefix(self.env), "transfers")

    def test_key_prefix_strips_border_slashes(self):
        """A leading slash produces keys like ``//transfers/...``; the orphan
        sweeper's prefix filter would then never match its own objects."""
        self.icp.set_param("bf_securetransfer.s3_key_prefix", "/transfers-bf/")
        self.assertEqual(s3.key_prefix(self.env), "transfers-bf")
        self.icp.set_param("bf_securetransfer.s3_key_prefix", "  /a/b/  ")
        self.assertEqual(s3.key_prefix(self.env), "a/b")

    def test_key_prefix_empty_falls_back(self):
        """An empty (or slash-only) value must not degrade to a bucket-wide
        prefix — that is a cross-tenant delete waiting to happen."""
        for raw in ("", "   ", "/", "///"):
            self.icp.set_param("bf_securetransfer.s3_key_prefix", raw)
            self.assertEqual(s3.key_prefix(self.env), "transfers", raw)

    # ------------------------------------------------------- 3. param / _int
    def test_param_returns_default_when_absent(self):
        """Callers rely on the default; a None here becomes a TypeError deep in
        the upload path."""
        self.assertEqual(
            s3.param(self.env, "does_not_exist_at_all", "fallback"), "fallback")
        self.assertIsNone(s3.param(self.env, "does_not_exist_at_all"))

    def test_int_param_non_numeric_falls_back(self):
        """A typo in a settings field ('90O' for '900') must not raise inside a
        presign call — the whole page would 500."""
        self.icp.set_param("bf_securetransfer.presign_put_ttl", "not-a-number")
        self.assertEqual(s3._int_param(self.env, "presign_put_ttl", 900), 900)
        self.icp.set_param("bf_securetransfer.presign_put_ttl", "")
        self.assertEqual(s3._int_param(self.env, "presign_put_ttl", 900), 900)

    def test_int_param_missing_key_falls_back(self):
        """Same guarantee for a key that was never written."""
        self.assertEqual(s3._int_param(self.env, "never_set_key", 42), 42)

    def test_int_param_reads_the_value(self):
        """Guard against over-correcting: a valid value must still win."""
        self.icp.set_param("bf_securetransfer.presign_put_ttl", "120")
        self.assertEqual(s3._int_param(self.env, "presign_put_ttl", 900), 120)

    # ------------------------------------------------------- 4. head_object
    def test_head_object_success(self):
        """finalize() compares the declared size with this one; a wrong parse
        would either accept truncated uploads or refuse valid ones."""
        c = self._mock_client()
        c.head_object.return_value = {"ContentLength": 4096, "ETag": '"abc123"'}
        got = s3.head_object(self.env, "transfers/t1/f1")
        c.head_object.assert_called_once_with(
            Bucket=BUCKET, Key="transfers/t1/f1")
        self.assertEqual(got, {"size": 4096, "etag": "abc123"})

    def test_head_object_absent_codes_return_none(self):
        """IDrive E2 answers HEAD-on-missing with 405, not 404. Any of these
        codes raising would turn an expired transfer into a 500 page."""
        c = self._mock_client()
        for code in ("404", "405", "NoSuchKey", "NotFound", "MethodNotAllowed"):
            c.head_object.side_effect = cerr(code, "HeadObject")
            self.assertIsNone(
                s3.head_object(self.env, "k"), "code %s must mean absent" % code)

    def test_head_object_absent_by_http_status_only(self):
        """Some responses carry no Error/Code, only the HTTP status — the
        fallback on ResponseMetadata is what keeps 404/405 recognised."""
        c = self._mock_client()
        err = CLIENT_ERROR({"ResponseMetadata": {"HTTPStatusCode": 405}}, "HeadObject")
        c.head_object.side_effect = err
        self.assertIsNone(s3.head_object(self.env, "k"))

    def test_head_object_other_error_is_reraised(self):
        """Swallowing AccessDenied would report every file as 'deleted' and let
        the purge cron wipe live transfers."""
        c = self._mock_client()
        c.head_object.side_effect = cerr("AccessDenied", "HeadObject")
        with self.assertRaises(CLIENT_ERROR):
            s3.head_object(self.env, "k")

    # ------------------------------------------------------- 5. delete_keys
    def test_delete_keys_chunks_by_1000(self):
        """S3 caps DeleteObjects at 1000 keys; a bigger batch is rejected
        wholesale and the purge silently stops deleting."""
        c = self._mock_client()
        c.delete_objects.return_value = {}
        keys = ["transfers/k%04d" % i for i in range(2500)]
        self.assertEqual(s3.delete_keys(self.env, keys), [])
        self.assertEqual(c.delete_objects.call_count, 3)
        sizes = [len(call.kwargs["Delete"]["Objects"])
                 for call in c.delete_objects.call_args_list]
        self.assertEqual(sizes, [1000, 1000, 500])
        first = c.delete_objects.call_args_list[0]
        self.assertEqual(first.kwargs["Bucket"], BUCKET)
        self.assertTrue(first.kwargs["Delete"]["Quiet"])
        self.assertEqual(first.kwargs["Delete"]["Objects"][0], {"Key": keys[0]})
        # every key was submitted exactly once, in order
        sent = [o["Key"] for call in c.delete_objects.call_args_list
                for o in call.kwargs["Delete"]["Objects"]]
        self.assertEqual(sent, keys)

    def test_delete_keys_empty_input_does_nothing(self):
        """A purge with nothing to delete must not issue a call (and must not
        crash on a None)."""
        c = self._mock_client()
        self.assertEqual(s3.delete_keys(self.env, []), [])
        self.assertEqual(s3.delete_keys(self.env, None), [])
        self.assertEqual(s3.delete_keys(self.env, ["", None]), [])
        c.delete_objects.assert_not_called()

    def test_delete_keys_missing_key_counts_as_success(self):
        """The goal is 'gone', not 'was there': counting NoSuchKey as a failure
        would keep a purged transfer forever in retry, incrementing
        purge_error_count until it alerts."""
        c = self._mock_client()
        c.delete_objects.return_value = {"Errors": [
            {"Key": "a", "Code": "NoSuchKey"},
            {"Key": "b", "Code": "NotFound"},
            {"Key": "c", "Code": "AccessDenied"},
        ]}
        self.assertEqual(s3.delete_keys(self.env, ["a", "b", "c"]), ["c"])

    def test_delete_keys_falls_back_to_per_object(self):
        """Some S3 clones refuse the batch API entirely; without the fallback
        the purge cron would never delete a single object on them."""
        c = self._mock_client()
        c.delete_objects.side_effect = cerr("NotImplemented", "DeleteObjects")
        failed = s3.delete_keys(self.env, ["a", "b"])
        self.assertEqual(failed, [])
        self.assertEqual(c.delete_object.call_count, 2)
        c.delete_object.assert_any_call(Bucket=BUCKET, Key="a")
        c.delete_object.assert_any_call(Bucket=BUCKET, Key="b")

    def test_delete_keys_fallback_tracks_real_failures(self):
        """In the fallback path a genuine denial must still be reported, while
        an already-absent key must not be."""
        c = self._mock_client()
        c.delete_objects.side_effect = cerr("NotImplemented", "DeleteObjects")

        def per_object(Bucket=None, Key=None):
            if Key == "gone":
                raise cerr("NoSuchKey", "DeleteObject")
            if Key == "denied":
                raise cerr("AccessDenied", "DeleteObject")
            return {}
        c.delete_object.side_effect = per_object
        self.assertEqual(
            s3.delete_keys(self.env, ["ok", "gone", "denied"]), ["denied"])

    # --------------------------------------------------------- 6. mpu_abort
    def test_mpu_abort_missing_upload_is_success(self):
        """The GC cron aborts stale uploads; raising on an upload the provider
        already expired would abort the whole sweep on its first item."""
        c = self._mock_client()
        for code in ("NoSuchUpload", "NotFound", "404"):
            c.abort_multipart_upload.side_effect = cerr(code, "AbortMPU")
            self.assertTrue(s3.mpu_abort(self.env, "k", "u1"), code)

    def test_mpu_abort_passes_the_right_arguments(self):
        """A missing UploadId aborts nothing and leaves paid-for storage
        accumulating invisibly."""
        c = self._mock_client()
        c.abort_multipart_upload.side_effect = None
        self.assertTrue(s3.mpu_abort(self.env, "transfers/x/f", "UP-1"))
        c.abort_multipart_upload.assert_called_once_with(
            Bucket=BUCKET, Key="transfers/x/f", UploadId="UP-1")

    def test_mpu_abort_other_error_is_reraised(self):
        """An AccessDenied must surface, not be reported as a clean abort."""
        c = self._mock_client()
        c.abort_multipart_upload.side_effect = cerr("AccessDenied", "AbortMPU")
        with self.assertRaises(CLIENT_ERROR):
            s3.mpu_abort(self.env, "k", "u1")

    # ------------------------------------------------------ 7. mpu_complete
    def test_mpu_complete_rebuilds_parts_from_list_parts(self):
        """Trusting client-reported ETags lets a browser complete an upload
        with parts it never wrote; the server-side ListParts is the only
        source of truth."""
        c = self._mock_client()
        c.list_parts.return_value = {
            "Parts": [
                {"PartNumber": 2, "ETag": '"etag-two"', "Size": 10},
                {"PartNumber": 1, "ETag": '"etag-one"', "Size": 5 * 1024 * 1024},
            ],
            "IsTruncated": False,
        }
        c.complete_multipart_upload.return_value = {"ETag": '"final-etag"'}
        etag = s3.mpu_complete(self.env, "transfers/x/f", "UP-1")
        self.assertEqual(etag, "final-etag")
        kwargs = c.complete_multipart_upload.call_args.kwargs
        self.assertEqual(kwargs["Bucket"], BUCKET)
        self.assertEqual(kwargs["Key"], "transfers/x/f")
        self.assertEqual(kwargs["UploadId"], "UP-1")
        # sorted by part number, quotes stripped, taken from ListParts
        self.assertEqual(kwargs["MultipartUpload"]["Parts"], [
            {"PartNumber": 1, "ETag": "etag-one"},
            {"PartNumber": 2, "ETag": "etag-two"},
        ])
        # the stitching call uses the long-timeout client
        self.assertTrue(
            any(call.kwargs.get("long_timeout")
                for call in self.client_factory.call_args_list),
            "CompleteMultipartUpload must use the long-timeout client")

    def test_mpu_complete_without_parts_raises(self):
        """Completing an empty upload creates a 0-byte object that then fails
        the size check at finalize with an unhelpful error."""
        c = self._mock_client()
        c.list_parts.return_value = {"Parts": [], "IsTruncated": False}
        with self.assertRaises(UserError):
            s3.mpu_complete(self.env, "k", "UP-1")
        c.complete_multipart_upload.assert_not_called()

    def test_mpu_sign_parts_signs_each_requested_part(self):
        """Wrong PartNumber/UploadId in the signature means every browser PUT
        of a large file gets a 403."""
        c = self._mock_client()
        c.generate_presigned_url.return_value = "https://signed.example.test/p"
        urls = s3.mpu_sign_parts(self.env, "transfers/x/f", "UP-1", [1, 2])
        self.assertEqual(sorted(urls), [1, 2])
        self.assertEqual(c.generate_presigned_url.call_count, 2)
        first = c.generate_presigned_url.call_args_list[0]
        self.assertEqual(first.args[0], "upload_part")
        self.assertEqual(first.kwargs["Params"], {
            "Bucket": BUCKET, "Key": "transfers/x/f",
            "UploadId": "UP-1", "PartNumber": 1,
        })
        self.assertEqual(first.kwargs["ExpiresIn"], 3600)

    def test_mpu_initiate_returns_upload_id(self):
        """A wrong key here uploads the parts somewhere the transfer will never
        find them."""
        c = self._mock_client()
        c.create_multipart_upload.return_value = {"UploadId": "UP-XYZ"}
        self.assertEqual(s3.mpu_initiate(self.env, "transfers/x/f"), "UP-XYZ")
        c.create_multipart_upload.assert_called_once_with(
            Bucket=BUCKET, Key="transfers/x/f")

    # ------------------------------------------------------- 8. pagination
    def test_mpu_list_parts_follows_pagination(self):
        """ListParts returns 1000 parts per page; stopping at the first page
        completes a multi-GB upload with only its beginning — silent corruption."""
        c = self._mock_client()
        c.list_parts.side_effect = [
            {"Parts": [{"PartNumber": 1, "ETag": '"e1"', "Size": 5}],
             "IsTruncated": True, "NextPartNumberMarker": 1},
            {"Parts": [{"PartNumber": 2, "ETag": '"e2"', "Size": 7}],
             "IsTruncated": False},
        ]
        parts = s3.mpu_list_parts(self.env, "transfers/x/f", "UP-1")
        self.assertEqual([p["n"] for p in parts], [1, 2])
        self.assertEqual([p["etag"] for p in parts], ["e1", "e2"])
        self.assertEqual([p["size"] for p in parts], [5, 7])
        self.assertEqual(c.list_parts.call_count, 2)
        self.assertNotIn("PartNumberMarker", c.list_parts.call_args_list[0].kwargs)
        self.assertEqual(
            c.list_parts.call_args_list[1].kwargs["PartNumberMarker"], 1)

    def test_list_objects_follows_pagination(self):
        """The orphan sweeper lists the whole prefix; stopping at page one
        leaves orphaned objects billed forever."""
        c = self._mock_client()
        c.list_objects_v2.side_effect = [
            {"Contents": [{"Key": "transfers/a", "Size": 1}],
             "IsTruncated": True, "NextContinuationToken": "TOK-2"},
            {"Contents": [{"Key": "transfers/b", "Size": 2}],
             "IsTruncated": False},
        ]
        out = s3.list_objects(self.env, "transfers/")
        self.assertEqual([o["key"] for o in out], ["transfers/a", "transfers/b"])
        self.assertEqual(c.list_objects_v2.call_count, 2)
        self.assertEqual(c.list_objects_v2.call_args_list[0].kwargs,
                         {"Bucket": BUCKET, "Prefix": "transfers/"})
        self.assertEqual(
            c.list_objects_v2.call_args_list[1].kwargs["ContinuationToken"],
            "TOK-2")

    def test_list_objects_stops_without_continuation_token(self):
        """IsTruncated without a token (some clones) must end the loop instead
        of spinning on the same page forever."""
        c = self._mock_client()
        c.list_objects_v2.return_value = {
            "Contents": [{"Key": "transfers/a", "Size": 1}],
            "IsTruncated": True,
        }
        out = s3.list_objects(self.env, "transfers/")
        self.assertEqual(len(out), 1)
        self.assertEqual(c.list_objects_v2.call_count, 1)

    def test_list_stale_mpus_follows_pagination(self):
        """Stale multipart uploads are invisible in ListObjects and billed as
        storage; missing page two leaves them there."""
        c = self._mock_client()
        old = datetime.now(timezone.utc) - timedelta(hours=48)
        c.list_multipart_uploads.side_effect = [
            {"Uploads": [{"Key": "transfers/a", "UploadId": "U1",
                          "Initiated": old}],
             "IsTruncated": True, "NextKeyMarker": "transfers/a",
             "NextUploadIdMarker": "U1"},
            {"Uploads": [{"Key": "transfers/b", "UploadId": "U2",
                          "Initiated": old}],
             "IsTruncated": False},
        ]
        stale = s3.list_stale_mpus(self.env, "transfers/", 24)
        self.assertEqual([s["upload_id"] for s in stale], ["U1", "U2"])
        self.assertEqual(c.list_multipart_uploads.call_count, 2)
        second = c.list_multipart_uploads.call_args_list[1].kwargs
        self.assertEqual(second["KeyMarker"], "transfers/a")
        self.assertEqual(second["UploadIdMarker"], "U1")
        self.assertEqual(second["Prefix"], "transfers/")

    def test_list_stale_mpus_respects_the_grace_window(self):
        """Aborting an upload still in flight destroys a legitimate transfer
        mid-upload."""
        c = self._mock_client()
        now = datetime.now(timezone.utc)
        c.list_multipart_uploads.return_value = {
            "Uploads": [
                {"Key": "fresh", "UploadId": "U-FRESH", "Initiated": now},
                {"Key": "old", "UploadId": "U-OLD",
                 "Initiated": now - timedelta(hours=48)},
            ],
            "IsTruncated": False,
        }
        stale = s3.list_stale_mpus(self.env, "transfers/", 24)
        self.assertEqual([s["upload_id"] for s in stale], ["U-OLD"])

    # -------------------------------------------------- 9. grace window
    def test_list_objects_respects_the_grace_window(self):
        """Without the cutoff the orphan sweeper deletes objects that a browser
        is uploading right now."""
        c = self._mock_client()
        now = datetime.now(timezone.utc)
        c.list_objects_v2.return_value = {
            "Contents": [
                {"Key": "transfers/fresh", "Size": 1, "LastModified": now},
                {"Key": "transfers/edge", "Size": 2,
                 "LastModified": now - timedelta(hours=23)},
                {"Key": "transfers/old", "Size": 3,
                 "LastModified": now - timedelta(hours=48)},
            ],
            "IsTruncated": False,
        }
        out = s3.list_objects(self.env, "transfers/", older_than_hours=24)
        self.assertEqual([o["key"] for o in out], ["transfers/old"])
        self.assertEqual(out[0]["size"], 3)
        # with no window, everything comes back
        self.assertEqual(
            len(s3.list_objects(self.env, "transfers/")), 3)

    # ------------------------------------------------- 10. is_endpoint_error
    def test_is_endpoint_error_true_for_network_failures(self):
        """The crons use this to abort cleanly and retry later; a False here
        turns a provider outage into thousands of error-counted transfers."""
        try:
            from botocore.exceptions import (
                ConnectTimeoutError, EndpointConnectionError, ReadTimeoutError,
            )
        except ImportError:
            self.skipTest("botocore absent on this image")
        for exc in (EndpointConnectionError(endpoint_url="https://x.test"),
                    ConnectTimeoutError(endpoint_url="https://x.test"),
                    ReadTimeoutError(endpoint_url="https://x.test")):
            self.assertTrue(s3.is_endpoint_error(exc), type(exc).__name__)

    def test_is_endpoint_error_false_for_other_exceptions(self):
        """Treating a permission error as an outage hides a real misconfiguration
        behind an endless 'retry later'."""
        self.assertFalse(s3.is_endpoint_error(ValueError("nope")))
        self.assertFalse(s3.is_endpoint_error(cerr("AccessDenied")))

    def test_is_endpoint_error_without_botocore(self):
        """It is called from except blocks: an ImportError here would mask the
        original exception on an image built without boto3."""
        with patch.dict(sys.modules, {"botocore.exceptions": None}):
            self.assertFalse(s3.is_endpoint_error(Exception("x")))

    # -------------------------------------------------------- 11. _boto3
    def test_boto3_missing_raises_actionable_usererror(self):
        """A raw ImportError at registry load makes the whole tenant
        un-upgradable; users must get a message naming the fix."""
        with patch.dict(sys.modules, {"boto3": None}):
            with self.assertRaises(UserError) as ctx:
                s3._boto3()
        msg = str(ctx.exception)
        self.assertIn("boto3", msg)
        self.assertIn("image", msg.lower())

    def test_boto3_missing_botocore_also_raises(self):
        """boto3 without botocore is a broken pip layer, not a working install."""
        with patch.dict(sys.modules, {"botocore": None}):
            with self.assertRaises(UserError):
                s3._boto3()

    # ------------------------------------------------ 12. _orphan_expiry_days
    def test_orphan_expiry_floor_is_60_days(self):
        """The provider-side net must stay behind the module's own purge; a
        shorter window deletes live transfers under the application's feet."""
        self._isolate_brands()
        for key in ("default_free_max_retention_days",
                    "default_paid_max_retention_days"):
            self.icp.set_param("bf_securetransfer." + key, "7")
        self.env["secure.transfer.brand"].create({
            "name": "Petite rétention", "domain": "floor.example.test",
            "max_retention_days": 7,
        })
        self.assertEqual(s3._orphan_expiry_days(self.env), 60)

    def test_orphan_expiry_follows_the_longest_brand_retention(self):
        """A brand granting 45 days: a flat rule would expire those files on
        their last day. The net is the longest retention + 15 days of slack."""
        self._isolate_brands()
        for key in ("default_free_max_retention_days",
                    "default_paid_max_retention_days"):
            self.icp.set_param("bf_securetransfer." + key, "7")
        self.env["secure.transfer.brand"].create({
            "name": "Longue rétention", "domain": "long.example.test",
            "max_retention_days": 120,
        })
        self.assertEqual(s3._orphan_expiry_days(self.env), 135)

    def test_orphan_expiry_accounts_for_tier_defaults(self):
        """A brand at 0 inherits the tier default from the parameters; ignoring
        those parameters silently shortens the net."""
        self._isolate_brands()
        self.icp.set_param("bf_securetransfer.default_free_max_retention_days", "7")
        self.icp.set_param("bf_securetransfer.default_paid_max_retention_days", "90")
        self.assertEqual(s3._orphan_expiry_days(self.env), 105)

    def test_orphan_expiry_survives_a_garbage_parameter(self):
        """A non-numeric parameter must not crash the bucket setup action."""
        self._isolate_brands()
        self.icp.set_param(
            "bf_securetransfer.default_free_max_retention_days", "beaucoup")
        self.icp.set_param("bf_securetransfer.default_paid_max_retention_days", "0")
        self.assertEqual(s3._orphan_expiry_days(self.env), 60)

    # ----------------------------------------------- 13. _tenant_cors_origins
    def test_tenant_cors_origins_union_dedup_sorted(self):
        """Every origin the upload page is served from needs CORS; a missing
        one makes browser PUTs fail with an opaque network error."""
        self._isolate_brands()
        self.icp.set_param(
            "bf_securetransfer.cors_origins",
            "  b.example.test , https://a.example.test ,, brand.example.test ")
        self.icp.set_param("bf_securetransfer.public_base_url",
                           "https://base.example.test/")
        self.env["secure.transfer.brand"].create({
            "name": "Marque active", "domain": "Brand.Example.Test",
        })
        self.env["secure.transfer.brand"].create({
            "name": "Marque archivée", "domain": "archived.example.test",
            "active": False,
        })
        origins = s3._tenant_cors_origins(self.env)
        self.assertEqual(origins, [
            "https://a.example.test",
            "https://b.example.test",
            "https://base.example.test",
            "https://brand.example.test",
        ])
        # scheme added where missing, trailing slash dropped, archived excluded
        self.assertNotIn("https://archived.example.test", origins)
        self.assertEqual(origins, sorted(set(origins)))

    def test_tenant_cors_origins_keeps_explicit_http_scheme(self):
        """A staging origin declared in http:// must not be rewritten to https,
        or its browser PUTs are blocked."""
        self._isolate_brands()
        self.icp.set_param("bf_securetransfer.cors_origins",
                           "http://localhost:8069")
        self.icp.set_param("bf_securetransfer.public_base_url", False)
        self.assertEqual(s3._tenant_cors_origins(self.env),
                         ["http://localhost:8069"])

    def test_tenant_cors_origins_empty_when_nothing_configured(self):
        """An empty list is what makes the CORS probe report a warning instead
        of writing an empty rule that locks every uploader out."""
        self._isolate_brands()
        self.icp.set_param("bf_securetransfer.cors_origins", False)
        self.icp.set_param("bf_securetransfer.public_base_url", False)
        self.assertEqual(s3._tenant_cors_origins(self.env), [])

    # -------------------------------------------------------- 14. apply_cors
    def test_apply_cors_preserves_other_tenants_origins(self):
        """PutBucketCors REPLACES the whole configuration: clobbering it on a
        shared bucket takes every other tenant's upload page offline."""
        self._isolate_brands()
        self.icp.set_param("bf_securetransfer.cors_origins", "https://mine.test")
        self.icp.set_param("bf_securetransfer.public_base_url", False)
        c = self._mock_client()
        c.get_bucket_cors.return_value = {"CORSRules": [
            {"AllowedOrigins": ["https://other-tenant.test",
                                "https://third-tenant.test"]},
            {"AllowedOrigins": ["https://mine.test"]},   # already ours
        ]}
        merged = s3.apply_cors(self.env)
        self.assertEqual(merged, ["https://mine.test",
                                  "https://other-tenant.test",
                                  "https://third-tenant.test"])
        kwargs = c.put_bucket_cors.call_args.kwargs
        self.assertEqual(kwargs["Bucket"], BUCKET)
        rule = kwargs["CORSConfiguration"]["CORSRules"][0]
        self.assertEqual(rule["AllowedOrigins"], merged)
        self.assertEqual(rule["AllowedMethods"], ["PUT"])
        self.assertEqual(rule["ExposeHeaders"], ["ETag"])

    def test_apply_cors_on_a_bucket_without_cors(self):
        """A fresh bucket answers NoSuchCORSConfiguration; raising there would
        make the setup action fail on the very first run."""
        self._isolate_brands()
        self.icp.set_param("bf_securetransfer.cors_origins", "https://mine.test")
        self.icp.set_param("bf_securetransfer.public_base_url", False)
        c = self._mock_client()
        c.get_bucket_cors.side_effect = cerr("NoSuchCORSConfiguration", "GetCors")
        self.assertEqual(s3.apply_cors(self.env), ["https://mine.test"])
        self.assertEqual(
            c.put_bucket_cors.call_args.kwargs["CORSConfiguration"]
            ["CORSRules"][0]["AllowedOrigins"], ["https://mine.test"])

    def test_apply_cors_reraises_real_errors(self):
        """An AccessDenied on GetBucketCors must not be mistaken for 'no CORS
        yet' — we would then overwrite a configuration we cannot read."""
        self._isolate_brands()
        self.icp.set_param("bf_securetransfer.cors_origins", "https://mine.test")
        c = self._mock_client()
        c.get_bucket_cors.side_effect = cerr("AccessDenied", "GetCors")
        with self.assertRaises(CLIENT_ERROR):
            s3.apply_cors(self.env)
        c.put_bucket_cors.assert_not_called()

    def test_apply_cors_without_origins_writes_nothing(self):
        """Writing a rule with an empty AllowedOrigins list would erase the
        origins already on a shared bucket."""
        self._isolate_brands()
        self.icp.set_param("bf_securetransfer.cors_origins", False)
        self.icp.set_param("bf_securetransfer.public_base_url", False)
        c = self._mock_client()
        self.assertEqual(s3.apply_cors(self.env), [])
        c.put_bucket_cors.assert_not_called()

    # ---------------------------------------------------- 15. probe_lifecycle
    def _setup_bucket_report(self, client_mock):
        """Run setup_bucket with the network probes neutralised."""
        self._start(patch.object(
            s3, "_http", side_effect=AssertionError("no network in tests")))
        return s3.setup_bucket(self.env)

    def test_lifecycle_preserves_foreign_rules(self):
        """PutBucketLifecycleConfiguration replaces everything: dropping the
        other tenants' rules removes their expiry net without any signal."""
        self._isolate_brands()
        self.icp.set_param("bf_securetransfer.s3_key_prefix", "transfers-bf")
        c = self._mock_client()
        c.get_bucket_lifecycle_configuration.return_value = {"Rules": [
            {"ID": "st-expire-orphans-transfers-prod", "Status": "Enabled"},
        ]}
        report = self._setup_bucket_report(c)
        self.assertTrue(report["lifecycle"]["ok"], report["lifecycle"]["detail"])
        rules = (c.put_bucket_lifecycle_configuration.call_args
                 .kwargs["LifecycleConfiguration"]["Rules"])
        ids = [r["ID"] for r in rules]
        self.assertIn("st-expire-orphans-transfers-prod", ids)
        self.assertIn("st-abort-incomplete-mpu-transfers-bf", ids)
        self.assertIn("st-expire-orphans-transfers-bf", ids)
        ours = next(r for r in rules if r["ID"] == "st-expire-orphans-transfers-bf")
        self.assertEqual(ours["Filter"]["Prefix"], "transfers-bf/")
        self.assertEqual(ours["Expiration"]["Days"],
                         s3._orphan_expiry_days(self.env))
        self.assertIn("1 règle(s) étrangère(s)", report["lifecycle"]["detail"])

    def test_lifecycle_falls_back_when_abort_rule_is_refused(self):
        """IDrive E2 rejects AbortIncompleteMultipartUpload with InvalidRequest.
        Sending both rules in one call got the WHOLE configuration refused, so
        no tenant ever had an expiry net."""
        self._isolate_brands()
        self.icp.set_param("bf_securetransfer.s3_key_prefix", "transfers-bf")
        c = self._mock_client()
        c.get_bucket_lifecycle_configuration.return_value = {"Rules": [
            {"ID": "foreign-rule", "Status": "Enabled"},
        ]}
        c.put_bucket_lifecycle_configuration.side_effect = [
            cerr("InvalidRequest", "PutLifecycle"), None,
        ]
        report = self._setup_bucket_report(c)
        self.assertTrue(report["lifecycle"]["ok"], report["lifecycle"]["detail"])
        self.assertEqual(c.put_bucket_lifecycle_configuration.call_count, 2)
        rules = (c.put_bucket_lifecycle_configuration.call_args_list[1]
                 .kwargs["LifecycleConfiguration"]["Rules"])
        self.assertEqual([r["ID"] for r in rules],
                         ["foreign-rule", "st-expire-orphans-transfers-bf"])
        self.assertNotIn(
            "AbortIncompleteMultipartUpload",
            {k for r in rules for k in r})
        self.assertIn("REFUSÉ", report["lifecycle"]["detail"])

    def test_lifecycle_on_a_bucket_without_configuration(self):
        """A fresh bucket answers NoSuchLifecycleConfiguration; that must be an
        empty starting point, not a failed probe."""
        self._isolate_brands()
        c = self._mock_client()
        c.get_bucket_lifecycle_configuration.side_effect = cerr(
            "NoSuchLifecycleConfiguration", "GetLifecycle")
        report = self._setup_bucket_report(c)
        self.assertTrue(report["lifecycle"]["ok"], report["lifecycle"]["detail"])
        rules = (c.put_bucket_lifecycle_configuration.call_args
                 .kwargs["LifecycleConfiguration"]["Rules"])
        self.assertEqual(len(rules), 2)
        self.assertIn("0 règle(s) étrangère(s)", report["lifecycle"]["detail"])

    def test_lifecycle_reraises_a_non_invalidrequest_error(self):
        """An AccessDenied must be reported as a failed probe, not swallowed
        into a 'lifecycle applied' success message."""
        self._isolate_brands()
        c = self._mock_client()
        c.get_bucket_lifecycle_configuration.return_value = {"Rules": []}
        c.put_bucket_lifecycle_configuration.side_effect = cerr(
            "AccessDenied", "PutLifecycle")
        report = self._setup_bucket_report(c)
        self.assertFalse(report["lifecycle"]["ok"])
        self.assertEqual(c.put_bucket_lifecycle_configuration.call_count, 1)

    def test_setup_bucket_probes_never_touch_the_network_in_tests(self):
        """Guard on the harness itself: every probe must report, none may raise
        out of setup_bucket."""
        self._isolate_brands()
        c = self._mock_client()
        report = self._setup_bucket_report(c)
        for name in ("location", "object_lock", "cors", "lifecycle",
                     "roundtrip", "server_side_put", "content_length_enforced",
                     "batch_delete", "multipart"):
            self.assertIn(name, report)
            self.assertIn("ok", report[name])

    # ------------------------------------------------- server-side PUT (backend)
    def test_put_bytes_pins_the_type_and_disposition_on_the_object(self):
        """The backend composer is the only upload that does NOT go through a
        presigned URL, so the download-time response overrides do not protect
        it on their own. Pinning octet-stream + attachment on the object means
        a bucket ever mis-set to public still cannot render an uploaded .svg or
        .html inline."""
        c = self._mock_client()
        c.put_object.return_value = {"ETag": '"abc123"'}
        etag = s3.put_bytes(self.env, "transfers/x/f", b"hello")
        self.assertEqual(etag, "abc123", "the quoted ETag must be unwrapped")
        kwargs = c.put_object.call_args.kwargs
        self.assertEqual(kwargs["Key"], "transfers/x/f")
        self.assertEqual(kwargs["Body"], b"hello")
        self.assertEqual(kwargs["ContentType"], "application/octet-stream")
        self.assertEqual(kwargs["ContentDisposition"], "attachment")

    def test_server_side_put_probe_reports_a_refusing_endpoint(self):
        """An S3-compatible endpoint may refuse PutObject (a read-only key, a
        bucket policy). The operator has to learn that from « Configurer le
        bucket S3 » — not from the first colleague who tries to attach a file
        to a secure send."""
        self._isolate_brands()
        c = self._mock_client()
        c.put_object.side_effect = Exception("Access Denied")
        report = self._setup_bucket_report(c)
        self.assertFalse(report["server_side_put"]["ok"])

    # -------------------------------------------------------- 16. presign_put
    def test_presign_put_signs_content_length_but_not_content_type(self):
        """Signing ContentLength is what stops an uploader from sending more
        bytes than the declared quota. Signing ContentType instead would let a
        browser pin text/html on an object and get it served inline."""
        c = self._mock_client()
        c.generate_presigned_url.return_value = "https://signed.example.test/put"
        out = s3.presign_put(self.env, "transfers/x/f", 4096)
        self.assertEqual(out["url"], "https://signed.example.test/put")
        self.assertEqual(out["headers"], {"Content-Length": "4096"})
        call = c.generate_presigned_url.call_args
        self.assertEqual(call.args[0], "put_object")
        self.assertEqual(call.kwargs["Params"], {
            "Bucket": BUCKET, "Key": "transfers/x/f", "ContentLength": 4096,
        })
        self.assertNotIn("ContentType", call.kwargs["Params"])
        self.assertEqual(call.kwargs["ExpiresIn"], 900)

    def test_presign_put_ttl_override(self):
        """A caller-supplied TTL must win; otherwise long uploads expire
        mid-flight."""
        c = self._mock_client()
        c.generate_presigned_url.return_value = "https://signed.example.test/put"
        s3.presign_put(self.env, "k", "2048", ttl=60)
        call = c.generate_presigned_url.call_args
        self.assertEqual(call.kwargs["ExpiresIn"], 60)
        self.assertEqual(call.kwargs["Params"]["ContentLength"], 2048)

    # -------------------------------------------------------- 17. presign_get
    def test_presign_get_forces_attachment_and_octet_stream(self):
        """Serving a stored file inline with its own MIME type is stored XSS on
        the storage domain: an uploaded .svg/.html would execute in the
        recipient's browser."""
        c = self._mock_client()
        c.generate_presigned_url.return_value = "https://signed.example.test/get"
        url = s3.presign_get(self.env, "transfers/x/f", "rapport.pdf", "text/html")
        self.assertEqual(url, "https://signed.example.test/get")
        call = c.generate_presigned_url.call_args
        self.assertEqual(call.args[0], "get_object")
        params = call.kwargs["Params"]
        self.assertEqual(params["Bucket"], BUCKET)
        self.assertEqual(params["Key"], "transfers/x/f")
        self.assertEqual(params["ResponseContentType"], "application/octet-stream")
        self.assertIn("attachment", params["ResponseContentDisposition"])
        self.assertIn("rapport.pdf", params["ResponseContentDisposition"])
        # the caller-provided mimetype is never reflected back
        self.assertNotIn("text/html", " ".join(str(v) for v in params.values()))
        self.assertEqual(call.kwargs["ExpiresIn"], 300)

    def test_presign_get_handles_unicode_and_missing_filename(self):
        """A Unicode filename must still produce a valid header (RFC 5987) and
        a missing one must not crash the download route."""
        c = self._mock_client()
        c.generate_presigned_url.return_value = "https://signed.example.test/get"
        s3.presign_get(self.env, "k", "Rapport Été.pdf", "application/pdf")
        disp = c.generate_presigned_url.call_args.kwargs["Params"][
            "ResponseContentDisposition"]
        self.assertIn("attachment", disp)
        self.assertIn("filename", disp)
        s3.presign_get(self.env, "k", "", "application/pdf")
        disp = c.generate_presigned_url.call_args.kwargs["Params"][
            "ResponseContentDisposition"]
        self.assertIn("attachment", disp)
        self.assertIn("fichier", disp)
