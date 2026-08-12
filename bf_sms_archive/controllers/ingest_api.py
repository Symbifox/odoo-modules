"""Public REST endpoint for the Android companion app (bf-sms-relay).

The phone POSTs batched SMS/MMS/call entries every 30s (charging) or 15 min
(on battery). Authentication is a Bearer token mapped to a ``sms.archive.device``
record, which carries the owner_id stamped on each created record.
"""
import json
import logging

from odoo import fields, http
from odoo.http import Response, request

_logger = logging.getLogger(__name__)

# Hard limits to keep one POST cheap
_MAX_ENTRIES = 1000
_MAX_BYTES = 50 * 1024 * 1024  # 50 MB
_MAX_ERROR_DETAIL = 10


def _json(payload, status=200):
    return Response(
        json.dumps(payload),
        status=status,
        content_type="application/json; charset=utf-8",
    )


class SmsArchiveIngestAPI(http.Controller):

    @http.route(
        "/bf_sms_archive/api/ingest",
        type="http",
        auth="public",
        methods=["POST"],
        csrf=False,
        save_session=False,
    )
    def ingest(self, **_kw):
        # 1. Auth
        auth_header = request.httprequest.headers.get("Authorization", "")
        token = auth_header[7:] if auth_header.startswith("Bearer ") else ""
        device = request.env["sms.archive.device"].sudo()._resolve(token)
        if not device:
            return _json({"error": "unauthorized"}, status=401)

        # 2. Payload guards
        raw = request.httprequest.data or b""
        if len(raw) > _MAX_BYTES:
            return _json({"error": "payload too large"}, status=413)
        try:
            payload = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            return _json({"error": "invalid json"}, status=400)

        entries = payload.get("entries")
        if not isinstance(entries, list):
            return _json({"error": "entries must be a list"}, status=400)
        if len(entries) > _MAX_ENTRIES:
            return _json(
                {"error": f"too many entries (max {_MAX_ENTRIES})"},
                status=400,
            )

        # 3. Dispatch
        owner_id = device.owner_id.id
        batch_id = f"android-{device.id}"
        Msg = request.env["sms.archive.message"].sudo()
        Call = request.env["call.archive.call"].sudo()

        created = duplicates = errors = 0
        error_details = []

        for idx, entry in enumerate(entries):
            try:
                kind = (entry or {}).get("kind")
                if kind == "sms":
                    _rec, was_new = Msg._ingest_one(
                        phone_raw=entry["phone"],
                        owner_id=owner_id,
                        direction=entry["direction"],
                        body=entry.get("body", ""),
                        date_ms=entry["date_ms"],
                        contact_name=entry.get("contact_name"),
                        is_mms=bool(entry.get("is_mms")),
                        parts=entry.get("parts"),
                        batch_id=batch_id,
                    )
                elif kind == "call":
                    _rec, was_new = Call._ingest_one(
                        phone_raw=entry["phone"],
                        owner_id=owner_id,
                        call_type=entry["call_type"],
                        date_ms=entry["date_ms"],
                        duration=entry.get("duration", 0),
                        contact_name=entry.get("contact_name"),
                        presentation=entry.get("presentation"),
                        batch_id=batch_id,
                    )
                else:
                    raise ValueError(f"unknown kind: {kind!r}")
                created += int(was_new)
                duplicates += int(not was_new)
            except KeyError as exc:
                errors += 1
                if len(error_details) < _MAX_ERROR_DETAIL:
                    error_details.append({"index": idx, "error": f"missing field {exc}"})
            except Exception as exc:
                errors += 1
                if len(error_details) < _MAX_ERROR_DETAIL:
                    error_details.append({"index": idx, "error": str(exc)})
                _logger.warning(
                    "ingest entry %s (device=%s) failed",
                    idx, device.id, exc_info=True,
                )

        device.sudo().write({
            "last_sync_at": fields.Datetime.now(),
            "last_sync_count": len(entries),
            "total_received": (device.total_received or 0) + created,
        })

        return _json({
            "created": created,
            "duplicates": duplicates,
            "errors": errors,
            "error_details": error_details,
        })
