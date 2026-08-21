"""S3 access layer for bf_securetransfer — the ONLY file that imports boto3.

Module-level functions (not a model): a thin, testable wrapper around the
S3-compatible endpoint (IDrive E2). boto3 is lazy-imported inside the
functions so the module stays installable on an image that was not rebuilt
yet — callers get a clean UserError instead of an ImportError at registry
load.

Credentials NEVER live in the database: access/secret keys come from the
environment (``BF_SECURETRANSFER_S3_ACCESS_KEY`` / ``_SECRET_KEY``) or from
``odoo.conf`` (``bf_securetransfer_s3_access_key`` / ``..._secret_key``).
Non-secret settings (endpoint, region, bucket, TTLs, ...) live in
``ir.config_parameter`` under the ``bf_securetransfer.`` prefix.
"""
import logging
import os
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone

from odoo import _
from odoo.exceptions import UserError
from odoo.http import content_disposition
from odoo.tools import config as odoo_config

_logger = logging.getLogger(__name__)

_TRUE_VALUES = ("1", "true", "yes", "on")


def _boto3():
    """Lazy import; raise a clean, actionable UserError if the tenant image
    was not rebuilt with the boto3 pip layer."""
    try:
        import boto3  # noqa: F401
        import botocore  # noqa: F401
    except ImportError:
        raise UserError(_(
            "La bibliothèque Python « boto3 » n'est pas installée sur cette "
            "instance. L'image Docker du tenant doit être reconstruite avec "
            "la couche pip boto3/botocore avant d'utiliser le transfert "
            "sécurisé."
        ))
    return boto3


def _client_error():
    """The botocore ClientError class (lazy, mirrors _boto3)."""
    _boto3()
    from botocore.exceptions import ClientError
    return ClientError


def is_endpoint_error(exc):
    """Whether an exception means the S3 endpoint is unreachable (network /
    timeout), i.e. the caller should abort its run cleanly and retry later.
    Safe to call even when boto3 is absent."""
    try:
        from botocore.exceptions import (
            ConnectTimeoutError,
            EndpointConnectionError,
            ReadTimeoutError,
        )
    except ImportError:
        return False
    return isinstance(
        exc, (EndpointConnectionError, ConnectTimeoutError, ReadTimeoutError)
    )


def param(env, key, default=None):
    """Read one ``bf_securetransfer.``-prefixed ir.config_parameter."""
    return env["ir.config_parameter"].sudo().get_param(
        "bf_securetransfer." + key, default
    )


def _int_param(env, key, default):
    try:
        return int(param(env, key, default) or default)
    except (TypeError, ValueError):
        return int(default)


def _tenant_cors_origins(env):
    """Every browser origin this tenant serves the upload page from: the
    explicit ``cors_origins`` param, the ``public_base_url``, and the domain
    of every active brand (so onboarding a paid custom domain and re-running
    the bucket setup automatically covers its CORS — no manual list to keep).
    Returns a de-duplicated, sorted list of ``https://host`` strings."""
    out = set()
    for raw in (param(env, "cors_origins", "") or "").split(","):
        raw = raw.strip()
        if raw:
            out.add(raw if raw.startswith(("http://", "https://")) else "https://" + raw)
    base = (param(env, "public_base_url", "") or "").strip().rstrip("/")
    if base:
        out.add(base if base.startswith(("http://", "https://")) else "https://" + base)
    try:
        brands = env["secure.transfer.brand"].sudo().search(
            [("active", "=", True), ("domain", "!=", False)])
        for b in brands:
            d = (b.domain or "").strip().strip("/").lower()
            if d:
                out.add("https://" + d)
    except Exception:  # noqa: BLE001 — model may not be loaded during setup
        pass
    return sorted(out)


def key_prefix(env):
    """Per-tenant root prefix for every S3 key this tenant writes.

    All environments may share ONE bucket (decision 2026-07-03): the prefix is
    what keeps each tenant's purge/MPU sweeps and lifecycle rules off the
    other tenants' objects. Configure ``bf_securetransfer.s3_key_prefix`` per
    tenant (e.g. ``transfers-bf`` / ``transfers-prod`` / ``transfers-staging``).
    """
    raw = (param(env, "s3_key_prefix", "transfers") or "transfers").strip()
    return raw.strip("/") or "transfers"


def _orphan_expiry_days(env):
    """How long the provider-side net waits before deleting anything still
    sitting under our prefix.

    It is a LAST RESORT behind ``_cron_purge_expired``, so it must stay above
    the longest legitimate retention — otherwise the net races the module's own
    purge and deletes a transfer that is still alive. (Client A ships a 45-day
    policy; a flat 45-day rule here would expire those files on their last
    day.) Hence: the longest retention any brand can grant, plus two weeks of
    slack, and never under 60 days.
    """
    longest = 0
    # active_test=False on purpose: archiving a brand does NOT expire the
    # transfers it already granted. Reading only active brands would shrink
    # the net back to 60 days while a 105-day transfer is still alive under
    # the prefix — the provider would then delete it before its promised date,
    # which is the exact failure this function exists to prevent.
    brands = env["secure.transfer.brand"].sudo().with_context(
        active_test=False).search([])
    if brands:
        longest = max(brands.mapped("max_retention_days") or [0])
    for key in ("default_free_max_retention_days",
                "default_paid_max_retention_days"):
        try:
            longest = max(longest, int(param(env, key, 0) or 0))
        except (TypeError, ValueError):
            pass
    return max(60, longest + 15)


def params(env):
    """Resolved connection parameters. Raises UserError on any missing piece
    (endpoint, bucket, credentials) with a message safe to surface."""
    endpoint_url = (param(env, "s3_endpoint_url") or "").strip()
    bucket = (param(env, "s3_bucket") or "").strip()
    if not endpoint_url or not bucket:
        raise UserError(_(
            "Le stockage S3 n'est pas configuré : renseignez l'endpoint et "
            "le bucket dans Configuration → Transfert sécurisé."
        ))
    access_key = (
        os.environ.get("BF_SECURETRANSFER_S3_ACCESS_KEY")
        or odoo_config.get("bf_securetransfer_s3_access_key")
    )
    secret_key = (
        os.environ.get("BF_SECURETRANSFER_S3_SECRET_KEY")
        or odoo_config.get("bf_securetransfer_s3_secret_key")
    )
    if not access_key or not secret_key:
        raise UserError(_(
            "Les clés d'accès S3 sont absentes. Déclarez "
            "bf_securetransfer_s3_access_key / bf_securetransfer_s3_secret_key "
            "dans odoo.conf (ou l'environnement) — jamais en base de données."
        ))
    path_style = str(param(env, "s3_path_style", "1")).strip().lower() in _TRUE_VALUES
    return {
        "endpoint_url": endpoint_url,
        "region": (param(env, "s3_region", "ca-east-1") or "ca-east-1").strip(),
        "bucket": bucket,
        "path_style": path_style,
        "access_key": access_key,
        "secret_key": secret_key,
        "put_ttl": _int_param(env, "presign_put_ttl", 900),
        "part_ttl": _int_param(env, "presign_part_ttl", 3600),
        "get_ttl": _int_param(env, "presign_get_ttl", 300),
    }


def client(env, long_timeout=False):
    """A configured boto3 S3 client (SigV4, path-style, bounded timeouts).

    ``long_timeout`` widens the read timeout for the few server-side calls
    that can legitimately be slow (CompleteMultipartUpload on huge objects).
    """
    boto3 = _boto3()
    from botocore.config import Config

    p = params(env)
    return boto3.client(
        "s3",
        endpoint_url=p["endpoint_url"],
        region_name=p["region"],
        aws_access_key_id=p["access_key"],
        aws_secret_access_key=p["secret_key"],
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path" if p["path_style"] else "virtual"},
            connect_timeout=5,
            read_timeout=180 if long_timeout else 30,
            retries={"max_attempts": 3, "mode": "standard"},
        ),
    )


# ------------------------------------------------------------------ presign
def presign_put(env, key, content_length, ttl=None):
    """Presigned simple PUT. ContentLength is part of the signature so the
    uploader cannot send more (or fewer) bytes than declared. ContentType is
    deliberately NOT signed: the served type is forced at download time."""
    p = params(env)
    url = client(env).generate_presigned_url(
        "put_object",
        Params={
            "Bucket": p["bucket"],
            "Key": key,
            "ContentLength": int(content_length),
        },
        ExpiresIn=int(ttl or p["put_ttl"]),
    )
    return {"url": url, "headers": {"Content-Length": str(int(content_length))}}


def presign_get(env, key, filename, mimetype, ttl=None):
    """Presigned GET for download. Content-Disposition: attachment (ASCII
    fallback + RFC 5987) and Content-Type forced to application/octet-stream
    so nothing ever renders inline — the stored-XSS killer. ``mimetype`` is
    accepted for API symmetry/logging but intentionally not reflected in the
    response headers."""
    p = params(env)
    return client(env).generate_presigned_url(
        "get_object",
        Params={
            "Bucket": p["bucket"],
            "Key": key,
            "ResponseContentDisposition": content_disposition(filename or "fichier"),
            "ResponseContentType": "application/octet-stream",
        },
        ExpiresIn=int(ttl or p["get_ttl"]),
    )


# ------------------------------------------------------------------ objects
def put_bytes(env, key, data):
    """Server-side PUT of ``data`` at ``key``. Returns the S3 ETag.

    ⚠ The public flow NEVER comes here: the browser PUTs straight to the bucket
    through a presigned URL, which is what keeps file content out of Odoo. This
    exists for the BACKEND composer only, where the operator's file necessarily
    arrives inside the Odoo request. Everything is held in memory, so the caller
    MUST cap the size first (see ``secure.transfer._backend_max_upload_bytes``).

    ``ContentType``/``ContentDisposition`` are pinned on the object itself, one
    notch stricter than the presigned path (which only forces them at download
    time via response overrides). If a bucket is ever mis-set to public, an
    object stored this way still cannot render inline in a browser.
    """
    p = params(env)
    resp = client(env, long_timeout=True).put_object(
        Bucket=p["bucket"],
        Key=key,
        Body=data,
        ContentType="application/octet-stream",
        ContentDisposition="attachment",
    )
    return (resp.get("ETag") or "").strip('"')


def head_object(env, key):
    """HEAD one key → ``{"size": int, "etag": str}`` or None when absent.

    IDrive E2 answers a HEAD on an absent key with **405 Method Not Allowed**
    (not 404), so 405 is treated as absent too — otherwise a deleted/expired
    object would raise instead of cleanly blocking the download."""
    p = params(env)
    try:
        resp = client(env).head_object(Bucket=p["bucket"], Key=key)
    except _client_error() as e:
        code = str(
            e.response.get("Error", {}).get("Code")
            or e.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            or ""
        )
        if code in ("404", "405", "NoSuchKey", "NotFound", "MethodNotAllowed"):
            return None
        raise
    return {
        "size": int(resp.get("ContentLength", 0)),
        "etag": (resp.get("ETag") or "").strip('"'),
    }


def delete_keys(env, keys):
    """Batch-delete keys (chunks of ≤1000). Returns the list of keys that
    FAILED to delete. A missing key (NoSuchKey) counts as success — the goal
    is 'gone', not 'was there'. Falls back to per-object deletes if the
    endpoint rejects the batch API."""
    keys = [k for k in (keys or []) if k]
    if not keys:
        return []
    p = params(env)
    c = client(env)
    failed = []
    for i in range(0, len(keys), 1000):
        chunk = keys[i:i + 1000]
        try:
            resp = c.delete_objects(
                Bucket=p["bucket"],
                Delete={"Objects": [{"Key": k} for k in chunk], "Quiet": True},
            )
            for err in resp.get("Errors", []):
                if err.get("Code") not in ("NoSuchKey", "NotFound"):
                    failed.append(err.get("Key"))
        except _client_error() as e:
            # Batch API refused (some S3 clones) — degrade to per-object.
            _logger.info(
                "bf_securetransfer: DeleteObjects rejeté (%s), repli objet "
                "par objet", e,
            )
            for k in chunk:
                try:
                    c.delete_object(Bucket=p["bucket"], Key=k)
                except _client_error() as e2:
                    code = e2.response.get("Error", {}).get("Code")
                    if code not in ("NoSuchKey", "NotFound"):
                        failed.append(k)
    return failed


# ------------------------------------------------------------------ multipart
def mpu_initiate(env, key):
    """CreateMultipartUpload → UploadId."""
    p = params(env)
    resp = client(env).create_multipart_upload(Bucket=p["bucket"], Key=key)
    return resp["UploadId"]


def mpu_sign_parts(env, key, upload_id, part_numbers):
    """Presigned UploadPart URLs → ``{part_number: url}``. Bounding of the
    part numbers/batch size is the caller's job (secure.transfer.file)."""
    p = params(env)
    c = client(env)
    urls = {}
    for n in part_numbers:
        urls[int(n)] = c.generate_presigned_url(
            "upload_part",
            Params={
                "Bucket": p["bucket"],
                "Key": key,
                "UploadId": upload_id,
                "PartNumber": int(n),
            },
            ExpiresIn=p["part_ttl"],
        )
    return urls


def mpu_list_parts(env, key, upload_id):
    """Paginated ListParts → ``[{"n": int, "etag": str, "size": int}]``.
    This is the server-side source of truth — client-reported ETags are
    never trusted."""
    p = params(env)
    c = client(env)
    parts = []
    kwargs = {}
    while True:
        resp = c.list_parts(
            Bucket=p["bucket"], Key=key, UploadId=upload_id, **kwargs
        )
        parts.extend(
            {
                "n": int(part["PartNumber"]),
                "etag": (part.get("ETag") or "").strip('"'),
                "size": int(part.get("Size", 0)),
            }
            for part in resp.get("Parts", [])
        )
        if resp.get("IsTruncated"):
            kwargs["PartNumberMarker"] = resp["NextPartNumberMarker"]
        else:
            return parts


def mpu_complete(env, key, upload_id):
    """CompleteMultipartUpload, rebuilding the part list from ListParts
    (never from the client). Returns the final ETag. Uses the long-timeout
    client: stitching a multi-GB object can be slow server-side."""
    parts = mpu_list_parts(env, key, upload_id)
    if not parts:
        raise UserError(_("Aucune partie téléversée pour ce fichier."))
    p = params(env)
    resp = client(env, long_timeout=True).complete_multipart_upload(
        Bucket=p["bucket"],
        Key=key,
        UploadId=upload_id,
        MultipartUpload={
            "Parts": [
                {"PartNumber": part["n"], "ETag": part["etag"]}
                for part in sorted(parts, key=lambda x: x["n"])
            ]
        },
    )
    return (resp.get("ETag") or "").strip('"')


def mpu_abort(env, key, upload_id):
    """AbortMultipartUpload. NoSuchUpload counts as success (already gone)."""
    p = params(env)
    try:
        client(env).abort_multipart_upload(
            Bucket=p["bucket"], Key=key, UploadId=upload_id
        )
    except _client_error() as e:
        code = e.response.get("Error", {}).get("Code")
        if code in ("NoSuchUpload", "NotFound", "404"):
            return True
        raise
    return True


def list_objects(env, prefix, older_than_hours=0):
    """Paginated ListObjectsV2 under ``prefix`` → ``[{"key", "last_modified",
    "size"}]``. When ``older_than_hours`` > 0, only objects last modified
    before that cutoff are returned (a grace window so an in-flight upload is
    never swept)."""
    p = params(env)
    c = client(env)
    cutoff = None
    if older_than_hours:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=older_than_hours)
    out = []
    kwargs = {"Bucket": p["bucket"], "Prefix": prefix}
    while True:
        resp = c.list_objects_v2(**kwargs)
        for obj in resp.get("Contents", []):
            lm = obj.get("LastModified")
            if cutoff and lm and lm >= cutoff:
                continue
            out.append({
                "key": obj["Key"],
                "last_modified": lm,
                "size": int(obj.get("Size", 0)),
            })
        if resp.get("IsTruncated") and resp.get("NextContinuationToken"):
            kwargs["ContinuationToken"] = resp["NextContinuationToken"]
        else:
            return out


def list_stale_mpus(env, prefix, older_than_hours):
    """Paginated ListMultipartUploads under ``prefix``, initiated more than
    ``older_than_hours`` ago → ``[{"key", "upload_id", "initiated"}]``."""
    p = params(env)
    c = client(env)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=older_than_hours)
    stale = []
    kwargs = {}
    while True:
        resp = c.list_multipart_uploads(
            Bucket=p["bucket"], Prefix=prefix, **kwargs
        )
        for up in resp.get("Uploads", []):
            initiated = up.get("Initiated")
            if initiated and initiated < cutoff:
                stale.append({
                    "key": up["Key"],
                    "upload_id": up["UploadId"],
                    "initiated": initiated,
                })
        if resp.get("IsTruncated"):
            kwargs["KeyMarker"] = resp.get("NextKeyMarker") or ""
            kwargs["UploadIdMarker"] = resp.get("NextUploadIdMarker") or ""
        else:
            return stale


# ------------------------------------------------------------------ provisioning
def _short(exc):
    return str(exc)[:300]


def _http(url, method="GET", data=None, headers=None, timeout=30):
    """Tiny stdlib HTTP helper for the presigned-URL probes (no external
    deps). Returns (status_code, body_bytes, headers)."""
    req = urllib.request.Request(url, data=data, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — presigned URL we just built
        return resp.status, resp.read(), dict(resp.headers)


def apply_cors(env):
    """Read-merge-write the bucket CORS so it covers every origin this tenant
    serves (param + public_base_url + active brand domains), without clobbering
    origins other tenants added. Returns the merged origin list. Idempotent —
    safe to call whenever a brand/domain changes."""
    ClientError = _client_error()
    p = params(env)
    c = client(env)
    origins = _tenant_cors_origins(env)
    if not origins:
        return []
    existing = []
    try:
        conf = c.get_bucket_cors(Bucket=p["bucket"])
        for rule in conf.get("CORSRules", []):
            existing.extend(rule.get("AllowedOrigins", []))
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code") or ""
        if code not in ("NoSuchCORSConfiguration", "404", "NotFound"):
            raise
    merged = sorted(set(existing) | set(origins))
    c.put_bucket_cors(
        Bucket=p["bucket"],
        CORSConfiguration={
            "CORSRules": [{
                "AllowedOrigins": merged,
                "AllowedMethods": ["PUT"],
                "AllowedHeaders": ["*"],
                "ExposeHeaders": ["ETag"],
                "MaxAgeSeconds": 3600,
            }]
        },
    )
    return merged


def setup_bucket(env):
    """Idempotent bucket provisioning + capability probes for IDrive E2.

    Applies CORS (PUT only, ExposeHeaders ETag) and tries the lifecycle
    safety net (non-fatal — the purge cron is the primary mechanism), then
    probes every behavior the module relies on. Returns a report dict
    ``{check: {"ok": bool, "detail": str}}`` for the settings notification
    and the server log.
    """
    ClientError = _client_error()
    p = params(env)
    c = client(env)
    report = {}
    # Probe scratch keys live UNDER this tenant's prefix (probes/ subfolder) so
    # the orphan sweeper's skip rule covers them and they never pollute the
    # shared bucket root. They self-clean, but this bounds a failed cleanup.
    probe_base = key_prefix(env) + "/probes"

    def run(name, fn):
        try:
            ok, detail = fn()
        except Exception as e:  # noqa: BLE001 — each probe reports, never aborts the run
            ok, detail = False, _short(e)
        report[name] = {"ok": bool(ok), "detail": detail}

    # -- residency proof: the bucket really lives in the configured region
    def probe_location():
        resp = c.get_bucket_location(Bucket=p["bucket"])
        loc = resp.get("LocationConstraint") or ""
        return loc == p["region"], "LocationConstraint=%r (attendu %r)" % (loc, p["region"])

    # -- object lock must be OFF, otherwise the purge promise is broken
    def probe_object_lock():
        try:
            conf = c.get_object_lock_configuration(Bucket=p["bucket"])
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code") or ""
            if "ObjectLockConfiguration" in code or code in ("404", "NotFound"):
                return True, "object lock absent (OFF) — purge possible"
            raise
        enabled = (
            conf.get("ObjectLockConfiguration", {}).get("ObjectLockEnabled")
            == "Enabled"
        )
        if enabled:
            return False, "object lock ACTIVÉ — la purge est impossible, recréer le bucket sans object lock"
        return True, "object lock désactivé"

    # -- CORS: browser PUTs need it; ExposeHeaders ETag is mandatory for MPU.
    # The bucket may be shared by several tenants, each running this action
    # with its own cors_origins param: MERGE with the origins already on the
    # bucket instead of clobbering them (PutBucketCors replaces everything).
    def probe_cors():
        merged = apply_cors(env)
        if not merged:
            return False, "aucune origine (param cors_origins vide et aucun domaine de marque) — CORS non appliqué"
        return True, "CORS appliqué (PUT seulement, ExposeHeaders ETag), origins fusionnées : %s" % ", ".join(merged)

    # -- lifecycle safety net behind the purge cron.
    # Shared-bucket aware: rule IDs carry the tenant prefix and rules from
    # OTHER prefixes are preserved (PutBucketLifecycleConfiguration replaces
    # the whole config, so this is a read-merge-write).
    #
    # IDrive E2 rejects AbortIncompleteMultipartUpload (InvalidRequest — it does
    # not validate that element) but accepts Expiration. Because both rules used
    # to travel in ONE call, the whole configuration was refused and NO lifecycle
    # was ever applied, on any tenant. They are now sent separately, so the abort
    # rule degrades to a reported warning instead of taking the expiry rule down
    # with it. What is lost is only the provider-side belt: stale multipart
    # uploads are already swept application-side by ``_cron_gc_drafts`` (see
    # ``list_stale_mpus``), which runs daily.
    def probe_lifecycle():
        prefix = key_prefix(env)
        expire_days = _orphan_expiry_days(env)
        abort_rule = {
            "ID": "st-abort-incomplete-mpu-%s" % prefix,
            "Status": "Enabled",
            "Filter": {"Prefix": prefix + "/"},
            "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 2},
        }
        expire_rule = {
            "ID": "st-expire-orphans-%s" % prefix,
            "Status": "Enabled",
            "Filter": {"Prefix": prefix + "/"},
            "Expiration": {"Days": expire_days},
        }
        our_ids = {abort_rule["ID"], expire_rule["ID"]}
        foreign = []
        try:
            conf = c.get_bucket_lifecycle_configuration(Bucket=p["bucket"])
            foreign = [
                r for r in conf.get("Rules", []) if r.get("ID") not in our_ids
            ]
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code") or ""
            if code not in ("NoSuchLifecycleConfiguration", "404", "NotFound"):
                raise

        def put(rules):
            c.put_bucket_lifecycle_configuration(
                Bucket=p["bucket"], LifecycleConfiguration={"Rules": rules},
            )

        try:
            put(foreign + [abort_rule, expire_rule])
        except ClientError as e:
            if (e.response.get("Error", {}).get("Code") or "") != "InvalidRequest":
                raise
            # The provider choked on the abort rule: keep the one that matters.
            put(foreign + [expire_rule])
            return True, (
                "expiration des orphelins posée sur %s/ à %d j ; abandon des "
                "multipart incomplets REFUSÉ par le fournisseur (non supporté) "
                "— sans effet ici, le module n'ouvre jamais de multipart. "
                "%d règle(s) étrangère(s) préservée(s)"
                % (prefix, expire_days, len(foreign))
            )
        return True, (
            "lifecycle appliqué sur %s/ (abort MPU 2 j + expiration %d j), "
            "%d règle(s) étrangère(s) préservée(s)"
            % (prefix, expire_days, len(foreign))
        )

    # -- full presigned round-trip: PUT → HEAD → GET → DELETE
    def probe_roundtrip():
        key = probe_base + "/setup-%s" % uuid.uuid4().hex
        body = b"bf_securetransfer round-trip probe"
        ps = presign_put(env, key, len(body))
        status, _body, _hdrs = _http(ps["url"], "PUT", data=body, headers=ps["headers"])
        if status not in (200, 201, 204):
            return False, "PUT présigné refusé (HTTP %s)" % status
        head = head_object(env, key)
        if not head or head["size"] != len(body):
            return False, "HEAD après PUT incohérent : %r" % (head,)
        url = presign_get(env, key, "probe.txt", "text/plain")
        status, got, hdrs = _http(url)
        if status != 200 or got != body:
            return False, "GET présigné incohérent (HTTP %s)" % status
        disp = (hdrs.get("Content-Disposition") or "").lower()
        if "attachment" not in disp:
            return False, "ResponseContentDisposition non honorée (%r)" % disp
        left = delete_keys(env, [key])
        if left:
            return False, "DELETE en échec pour %s" % left
        return True, "PUT/HEAD/GET/DELETE présignés OK, disposition attachment honorée"

    # -- server-side PUT: the backend composer's own path (put_bytes). It is
    #    the ONE upload that does not go through a presigned URL, so the
    #    presigned round-trip above says nothing about it — and it pins
    #    Content-Type/Content-Disposition ON THE OBJECT, which an S3-compatible
    #    endpoint is free not to honour. Probed here rather than assumed:
    #    "Configurer le bucket S3" is where a tenant finds out, not the first
    #    operator who tries to attach a file.
    def probe_server_side_put():
        key = probe_base + "/ssp-%s" % uuid.uuid4().hex
        body = b"bf_securetransfer server-side put probe"
        etag = put_bytes(env, key, body)
        if not etag:
            return False, "PutObject sans ETag en réponse"
        head = head_object(env, key)
        if not head or head["size"] != len(body):
            return False, "HEAD après PutObject incohérent : %r" % (head,)
        if head["etag"] != etag:
            return False, "ETag PutObject ≠ ETag HEAD (%s / %s)" % (etag, head["etag"])
        url = presign_get(env, key, "probe.txt", "text/plain")
        status, got, _hdrs = _http(url)
        if status != 200 or got != body:
            return False, "relecture du PUT serveur incohérente (HTTP %s)" % status
        left = delete_keys(env, [key])
        if left:
            return False, "DELETE en échec pour %s" % left
        return True, ("PutObject serveur OK (envoi backend), ETag épinglable, "
                      "relecture conforme")

    # -- is the signed Content-Length actually enforced by the endpoint?
    def probe_content_length():
        key = probe_base + "/cl-%s" % uuid.uuid4().hex
        ps = presign_put(env, key, 8)          # signed for 8 bytes
        oversize = b"0123456789ABCDEF"          # send 16
        try:
            status, _body, _hdrs = _http(ps["url"], "PUT", data=oversize)
            enforced = False
            detail = ("le endpoint a ACCEPTÉ un corps plus gros que la taille signée "
                      "(HTTP %s) — les quotas déclarés ne sont pas garantis côté stockage" % status)
        except urllib.error.HTTPError as e:
            enforced = e.code in (400, 403)
            detail = "corps sur-dimensionné rejeté (HTTP %s) — Content-Length signé appliqué" % e.code
        delete_keys(env, [key])  # best-effort cleanup either way
        return enforced, detail

    # -- batch DeleteObjects support (the purge cron's fast path)
    def probe_batch_delete():
        k1 = probe_base + "/bd1-%s" % uuid.uuid4().hex
        k2 = probe_base + "/bd2-%s" % uuid.uuid4().hex
        c.put_object(Bucket=p["bucket"], Key=k1, Body=b"x")
        c.put_object(Bucket=p["bucket"], Key=k2, Body=b"x")
        left = delete_keys(env, [k1, k2])
        if left:
            return False, "échec de suppression par lot : %s" % left
        if head_object(env, k1) is not None:
            return False, "objet encore présent après DeleteObjects"
        return True, "DeleteObjects par lot OK"

    # -- full multipart cycle: initiate → sign → PUT part → list → complete
    def probe_multipart():
        key = probe_base + "/mpu-%s" % uuid.uuid4().hex
        body = b"m" * 1024  # a single (last) part may be < 5 MiB
        upload_id = mpu_initiate(env, key)
        try:
            urls = mpu_sign_parts(env, key, upload_id, [1])
            status, _b, hdrs = _http(urls[1], "PUT", data=body)
            if status not in (200, 201, 204):
                return False, "PUT de partie refusé (HTTP %s)" % status
            if not (hdrs.get("ETag") or "").strip('"'):
                return False, "ETag absent de la réponse UploadPart"
            parts = mpu_list_parts(env, key, upload_id)
            if len(parts) != 1 or parts[0]["size"] != len(body):
                return False, "ListParts incohérent : %r" % (parts,)
            etag = mpu_complete(env, key, upload_id)
            head = head_object(env, key)
            if not head or head["size"] != len(body):
                return False, "HEAD après CompleteMultipartUpload incohérent"
            delete_keys(env, [key])
            return True, "cycle multipart complet OK (ETag final %s…)" % etag[:8]
        except Exception:
            mpu_abort(env, key, upload_id)
            raise

    run("location", probe_location)
    run("object_lock", probe_object_lock)
    run("cors", probe_cors)
    run("lifecycle", probe_lifecycle)
    run("roundtrip", probe_roundtrip)
    run("server_side_put", probe_server_side_put)
    run("content_length_enforced", probe_content_length)
    run("batch_delete", probe_batch_delete)
    run("multipart", probe_multipart)
    return report
