# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import hmac
import json
import logging
import re
from datetime import datetime, timezone

from odoo import fields, http
from odoo.http import request

_logger = logging.getLogger(__name__)


def _parse_iso8601(s):
    """Parse '2026-04-30T12:05:10Z' or '...+00:00' into a naive UTC datetime (Odoo storage format)."""
    if not s:
        return False
    try:
        s2 = s.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s2)
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    except (ValueError, TypeError):
        return False


class BackupAPIController(http.Controller):
    """API endpoints for receiving backup reports from external systems."""

    # ── Auth helper ──────────────────────────────────────────────────────

    def _validate_token(self):
        """Validate X-Backup-Token header. Returns (ok, error_response_or_None)."""
        token = request.httprequest.headers.get("X-Backup-Token")
        expected_token = (
            request.env["ir.config_parameter"]
            .sudo()
            .get_param("hosting.backup_api_token", "")
        )
        if not expected_token or expected_token == "CHANGE_ME_TO_SECURE_TOKEN":
            return (
                False,
                request.make_json_response(
                    {"success": False, "error": "API token not configured"},
                    status=500,
                ),
            )
        if not token or not hmac.compare_digest(token, expected_token):
            return (
                False,
                request.make_json_response(
                    {"success": False, "error": "Invalid API token"},
                    status=401,
                ),
            )
        return (True, None)

    # ── Backup report endpoints ──────────────────────────────────────────

    @http.route(
        "/api/hosting/backup/report",
        type="http",
        auth="api_key",
        methods=["POST"],
        csrf=False,
    )
    def receive_backup_report(self, **kwargs):
        """Backup report endpoint, api_key auth.

        api_key auth only proves *who* the caller is — any internal user can mint
        an API key for themselves. Backup telemetry is created as sudo, so without
        an authorization check any user could forge success/failure runs and
        trigger report emails/ntfy. Require the hosting-manager group. (Real
        backup agents use /report/public with the X-Backup-Token instead.)"""
        if not request.env.user.has_group(
            "hosting_management.group_hosting_manager"
        ):
            return request.make_json_response(
                {"success": False, "error": "Forbidden"}, status=403,
            )
        return self._dispatch_backup_report()

    @http.route(
        "/api/hosting/backup/report/public",
        type="http",
        auth="public",
        methods=["POST"],
        csrf=False,
    )
    def receive_backup_report_public(self, **kwargs):
        """Backup report endpoint, X-Backup-Token auth."""
        ok, err = self._validate_token()
        if not ok:
            return err
        return self._dispatch_backup_report()

    def _dispatch_backup_report(self):
        """Parse JSON body, route to legacy or restic handler based on report_type."""
        try:
            data = json.loads(request.httprequest.data or b"{}")
        except (json.JSONDecodeError, ValueError) as e:
            return request.make_json_response(
                {"success": False, "error": f"Invalid JSON: {e}"},
                status=400,
            )

        report_type = data.get("report_type", "legacy")
        try:
            if report_type == "restic":
                return self._handle_restic_report(data)
            return self._handle_legacy_report(data)
        except Exception:
            _logger.exception(
                "Error processing backup report (type=%s)", report_type
            )
            return request.make_json_response(
                {"success": False, "error": "Internal server error"},
                status=500,
            )

    # ── Legacy handler (preserved as-is) ─────────────────────────────────

    def _handle_legacy_report(self, data):
        """Legacy ZIP pipeline — unchanged behavior, including action_send_report."""
        _logger.info(
            "Received legacy backup report from %s",
            data.get("hostname", "unknown"),
        )

        run_vals = {
            "hostname": data.get("hostname"),
            "backup_root": data.get("backup_root"),
            "report_type": "legacy",
            "notes": f"Received via API at {data.get('timestamp')}",
        }

        timestamp = data.get("timestamp")
        if timestamp:
            try:
                run_vals["run_date"] = datetime.strptime(
                    timestamp, "%Y-%m-%d %H:%M:%S"
                )
            except (ValueError, TypeError):
                pass

        run = request.env["hosting.backup.run"].sudo().create(run_vals)

        results = data.get("results", [])
        for result in results:
            line_vals = {
                "run_id": run.id,
                "service_name": result.get("service", "Unknown"),
                "status": result.get("status", "failed"),
                "duration": result.get("duration", "-"),
                "error_message": result.get("error", ""),
                "container_count": result.get("container_count", 0),
                "verified_file_count": result.get("verified_count", 0),
            }
            line = request.env["hosting.backup.line"].sudo().create(line_vals)

            for file_data in result.get("files", []):
                request.env["hosting.backup.file"].sudo().create({
                    "line_id": line.id,
                    "name": file_data.get("name", ""),
                    "size": file_data.get("size", ""),
                    "checksum": file_data.get("checksum", ""),
                    "verified": file_data.get("verified", False),
                })

        _logger.info(
            "Created legacy backup run %s with %d lines", run.name, len(results)
        )

        # En mode planifié, le cron `_cron_send_daily_report` envoie le courriel.
        # Le contrôleur n'envoie inline qu'en mode immédiat.
        send_email = (
            request.env["ir.config_parameter"]
            .sudo()
            .get_param("hosting.restic_send_email_report", "1")
            == "1"
        )
        if send_email:
            run.action_send_report()
        else:
            run._maybe_send_ntfy_alert()

        return request.make_json_response({
            "success": True,
            "backup_run_id": run.id,
            "backup_run_name": run.name,
            "report_type": "legacy",
            "message": "Backup report created and email sent",
        })

    # ── Restic handler ────────────────────────────────────────────────────

    def _handle_restic_report(self, data):
        """Handle Restic master report (multi-snapshot per service line)."""
        hostname = data.get("hostname") or "unknown"
        _logger.info("Received Restic backup report from %s", hostname)

        timestamp = data.get("timestamp")
        run_date = False
        if timestamp:
            try:
                run_date = datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S")
            except (ValueError, TypeError):
                pass

        # Idempotence on (hostname, run_date, restic)
        if run_date:
            existing = (
                request.env["hosting.backup.run"]
                .sudo()
                .search(
                    [
                        ("hostname", "=", hostname),
                        ("run_date", "=", run_date),
                        ("report_type", "=", "restic"),
                    ],
                    limit=1,
                )
            )
            if existing:
                return request.make_json_response({
                    "success": True,
                    "idempotent": True,
                    "backup_run_id": existing.id,
                    "backup_run_name": existing.name,
                })

        run_vals = {
            "hostname": hostname,
            "report_type": "restic",
            "notes": f"Received via API at {timestamp}",
        }
        if run_date:
            run_vals["run_date"] = run_date

        run = request.env["hosting.backup.run"].sudo().create(run_vals)

        Repo = request.env["hosting.backup.repository"].sudo()
        Snapshot = request.env["hosting.backup.snapshot"].sudo()
        unknown_destinations = set()

        for result in data.get("results", []):
            line_vals = {
                "run_id": run.id,
                "service_name": result.get("service", "Unknown"),
                "status": result.get("status", "failed"),
                "duration": result.get("duration", "-"),
                "error_message": result.get("error", ""),
                "exit_code": int(result.get("exit_code") or 0),
            }
            line = request.env["hosting.backup.line"].sudo().create(line_vals)

            for snap in result.get("snapshots") or []:
                destination = snap.get("destination") or ""
                repo = (
                    Repo.search([("name", "=", destination)], limit=1)
                    if destination
                    else Repo.browse()
                )
                if not repo and destination:
                    unknown_destinations.add(destination)
                    _logger.warning(
                        "Unknown Restic destination '%s' (snapshot=%s, container=%s)",
                        destination,
                        snap.get("snapshot_id"),
                        snap.get("container"),
                    )

                Snapshot.create({
                    "repository_id": repo.id if repo else False,
                    "snapshot_id": snap.get("snapshot_id") or "?",
                    "snapshot_date": run.run_date or fields.Datetime.now(),
                    "container": snap.get("container") or "",
                    "destination": destination,
                    "files_new": int(snap.get("files_new") or 0),
                    "data_added_bytes": float(snap.get("data_added") or 0),
                    "duration_sec": float(snap.get("duration_sec") or 0),
                    "run_line_id": line.id,
                })

        _logger.info(
            "Created Restic backup run %s with %d lines",
            run.name,
            len(data.get("results", [])),
        )

        send_email = (
            request.env["ir.config_parameter"]
            .sudo()
            .get_param("hosting.restic_send_email_report", "0")
            == "1"
        )
        if send_email:
            try:
                run.action_send_report()
            except Exception:
                _logger.exception("Error sending Restic backup email")
        else:
            run._maybe_send_ntfy_alert()

        return request.make_json_response({
            "success": True,
            "backup_run_id": run.id,
            "backup_run_name": run.name,
            "report_type": "restic",
            "lines_created": len(data.get("results", [])),
            "unknown_destinations": sorted(unknown_destinations),
        })

    # ── Watchdog endpoint ────────────────────────────────────────────────

    @http.route(
        "/api/hosting/backup/watchdog/public",
        type="http",
        auth="public",
        methods=["POST"],
        csrf=False,
    )
    def receive_watchdog_state(self, **kwargs):
        """Receive Restic watchdog state. Payload = watchdog-state.json contents."""
        ok, err = self._validate_token()
        if not ok:
            return err

        try:
            data = json.loads(request.httprequest.data or b"{}")
        except (json.JSONDecodeError, ValueError) as e:
            return request.make_json_response(
                {"success": False, "error": f"Invalid JSON: {e}"},
                status=400,
            )

        try:
            collected_at = (
                _parse_iso8601(data.get("collected_iso")) or fields.Datetime.now()
            )
            host = data.get("host") or "unknown"

            Repo = request.env["hosting.backup.repository"].sudo()
            repos_payload = data.get("repos") or {}
            updated = []
            unknown = []
            for name, info in repos_payload.items():
                repo = Repo.search([("name", "=", name)], limit=1)
                if not repo:
                    unknown.append(name)
                    _logger.warning(
                        "Watchdog: unknown repository '%s' — skipped", name
                    )
                    continue
                repo.write({
                    "snapshot_count": int(info.get("snapshot_count") or 0),
                    "latest_snapshot_date": _parse_iso8601(
                        info.get("latest_snapshot_iso")
                    ),
                    "size_bytes": float(info.get("size_bytes") or 0),
                    "last_watchdog_sync": collected_at,
                })
                updated.append(name)

            bucket = data.get("bucket") or {}
            request.env["hosting.backup.bucket.snapshot"].sudo().create({
                "collected_at": collected_at,
                "host": host,
                "total_objects": int(bucket.get("total_objects") or 0),
                "total_bytes": float(bucket.get("total_bytes") or 0),
            })

            return request.make_json_response({
                "success": True,
                "repos_updated": len(updated),
                "repos_unknown": unknown,
            })
        except Exception:
            _logger.exception("Error processing watchdog state")
            return request.make_json_response(
                {"success": False, "error": "Internal server error"},
                status=500,
            )
