# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import hmac
import json
import logging
import re
from datetime import datetime, timezone

from odoo import fields, http
from odoo.http import request

from ..models.hosting_backup_log import _icp_truthy

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
            if report_type == "saas":
                return self._handle_saas_report(data)
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

        # ⚠️ Lecture TOLÉRANTE : voir `_icp_truthy`. Un `== "1"` ici rendait le
        # rapport muet dès qu'on enregistrait la page des réglages, qui écrit la
        # chaîne « True ». Même famille que le piège décrit dans `_icp_truthy`,
        # et que le réveil du softphone, éteint huit jours par un seul clic.
        send_email = _icp_truthy(
            request.env["ir.config_parameter"]
            .sudo()
            .get_param("hosting.restic_send_email_report"),
            default=False,
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

    # ── Sauvegardes infonuagiques (produit tiers) ────────────────────────

    #: Les verdicts que les produits écrivent, ramenés aux nôtres. Un mot
    #: inconnu ne devient pas « Réussie » par défaut : il tombe sur
    #: « Indéterminée » et le mot d'origine est gardé dans `result_raw`.
    _SAAS_RESULT_MAP = {
        "succeeded": "succeeded",
        "success": "succeeded",
        "finishedwitherrors": "with_errors",
        "finished_with_errors": "with_errors",
        "with_errors": "with_errors",
        "canceled": "canceled",
        "cancelled": "canceled",
        "failed": "failed",
        "error": "failed",
    }

    def _handle_saas_report(self, data):
        """Verser l'état d'un produit de sauvegarde tiers, une entrée par
        organisation et par exécution.

        ⚠️ Ce gestionnaire n'écrit RIEN dans `hosting.backup.run` : trois
        lecteurs y prennent « la dernière exécution » sans filtrer le type, et
        une nuit infonuagique versée là les ferait noter la mauvaise. Voir
        `models/hosting_saas_backup.py`.

        Il n'envoie ni courriel ni ntfy : l'alerte n'est pas encore
        décidée. Un lecteur qui verse trois mois d'archives d'un coup ne doit
        réveiller personne.
        """
        provider = (data.get("provider") or "cubebackup").strip()
        hostname = data.get("hostname") or "unknown"
        ingest_source = data.get("source") or "log"
        _logger.info(
            "Rapport de sauvegarde infonuagique reçu de %s (produit=%s, source=%s)",
            hostname, provider, ingest_source,
        )

        Run = request.env["hosting.saas.backup.run"].sudo()
        Failure = request.env["hosting.saas.backup.failure"].sudo()
        Service = request.env["hosting.service"].sudo()

        created, updated, skipped = 0, 0, 0
        unmatched = set()
        touched = Run.browse()

        for item in data.get("runs") or []:
            org_ref = (item.get("organization_ref") or "").strip()
            external_id = str(item.get("external_id") or "").strip()
            if not org_ref or not external_id:
                skipped += 1
                _logger.warning(
                    "Exécution ignorée : organisation ou tâche sans identifiant "
                    "(org=%r, tâche=%r)", org_ref, external_id,
                )
                continue

            raw_result = (item.get("result") or "").strip()
            result = self._SAAS_RESULT_MAP.get(raw_result.lower(), "unknown")

            service = Service.search(
                [("saas_backup_ref", "=", org_ref)], limit=1,
            )
            if not service:
                unmatched.add(org_ref)

            vals = {
                "provider": provider,
                "organization_ref": org_ref,
                "organization_name": item.get("organization_name") or "",
                "service_id": service.id if service else False,
                "external_id": external_id,
                "run_date": _parse_iso8601(item.get("started_at")) or fields.Datetime.now(),
                "end_date": _parse_iso8601(item.get("finished_at")),
                "duration_sec": float(item.get("duration_sec") or 0.0),
                "result": result,
                "result_raw": raw_result,
                "apps_total": int(item.get("apps_total") or 0),
                "apps_failed": int(item.get("apps_failed") or 0),
                "ingest_source": ingest_source,
                "hostname": hostname,
            }

            existing = Run.search([
                ("provider", "=", provider),
                ("organization_ref", "=", org_ref),
                ("external_id", "=", external_id),
            ], limit=1)
            if existing:
                existing.write(vals)
                existing.failure_ids.unlink()
                run = existing
                updated += 1
            else:
                run = Run.create(vals)
                created += 1
            touched |= run

            for failure in item.get("failures") or []:
                Failure.create({
                    "run_id": run.id,
                    "app_type": (failure.get("app_type") or "")[:64],
                    "subject_name": (failure.get("subject_name") or "")[:256],
                    "subject_login": (failure.get("subject_login") or "")[:256],
                    "subject_ref": (failure.get("subject_ref") or "")[:128],
                    "error_code": (failure.get("error_code") or "")[:128],
                    "http_code": str(failure.get("http_code") or "")[:16],
                    "error_message": (failure.get("error_message") or "")[:512],
                })

        readings = self._handle_saas_readings(data, provider)

        if touched:
            touched._sync_service_state()

        if unmatched:
            _logger.warning(
                "Organisations sans fiche de service : %s", sorted(unmatched),
            )

        return request.make_json_response({
            "success": True,
            "report_type": "saas",
            "provider": provider,
            "created": created,
            "updated": updated,
            "skipped": skipped,
            "unmatched_organizations": sorted(unmatched),
            "readings_created": readings["created"],
            "readings_updated": readings["updated"],
            "readings_unmatched": sorted(readings["unmatched"]),
        })

    def _handle_saas_readings(self, data, provider):
        """Verser les relevés périodiques (licence, stockage, compteurs).

        Un relevé n'est pas une exécution : c'est ce que le fournisseur AFFIRME
        sur une période. Il vit à côté, il ne corrige jamais une exécution, et
        il ne sert jamais à conclure qu'une nuit a tourné.
        """
        Reading = request.env["hosting.saas.backup.reading"].sudo()
        Line = request.env["hosting.saas.backup.reading.line"].sudo()
        Run = request.env["hosting.saas.backup.run"].sudo()
        License = request.env["hosting.license"].sudo()

        created = updated = 0
        unmatched = set()
        latest = None

        for item in data.get("readings") or []:
            period_end = item.get("period_end")
            if not period_end:
                continue
            vals = {
                "provider": provider,
                "period_start": item.get("period_start") or False,
                "period_end": period_end,
                "seats_total": int(item.get("seats_total") or 0),
                "seats_used": int(item.get("seats_used") or 0),
                "license_expiry": item.get("license_expiry") or False,
                "storage_kind": (item.get("storage_kind") or "")[:128],
                "storage_location": (item.get("storage_location") or "")[:256],
                "storage_available": (item.get("storage_available") or "")[:128],
                "index_path": (item.get("index_path") or "")[:256],
                "index_free_display": (item.get("index_free_display") or "")[:128],
                "index_free_pct": float(item.get("index_free_pct") or 0.0),
                "ingest_source": item.get("source") or "email",
                "source_ref": str(item.get("source_ref") or "")[:64],
            }
            existing = Reading.search([
                ("provider", "=", provider), ("period_end", "=", period_end),
            ], limit=1)
            if existing:
                existing.write(vals)
                existing.line_ids.unlink()
                reading = existing
                updated += 1
            else:
                reading = Reading.create(vals)
                created += 1

            for line in item.get("organizations") or []:
                name = (line.get("organization_name") or "").strip()
                if not name:
                    continue
                # ⚠️ Le rapport ne porte que le NOM. On le rattache par la
                # dernière exécution qui portait ce nom, parce qu'elle, elle a
                # les deux. Sans correspondance, la ligne reste non rattachée :
                # une ligne orpheline se voit, une ligne mal rattachée ment.
                run = Run.search([
                    ("provider", "=", provider),
                    ("organization_name", "=", name),
                    ("service_id", "!=", False),
                ], order="run_date desc", limit=1)
                if not run:
                    unmatched.add(name)
                Line.create({
                    "reading_id": reading.id,
                    "organization_name": name[:256],
                    "service_id": run.service_id.id if run else False,
                    "errors": int(line.get("errors") or 0),
                    "items": int(line.get("items") or 0),
                    "size_display": (line.get("size_display") or "")[:64],
                })

            # ⚠️ Comparer les CHAMPS, pas la charge : `period_end` arrive en
            # chaîne et le champ rend une date. `"2026-07-27" > date(...)`
            # lève un TypeError, et le versement entier tombe en 500 dès qu'un
            # rattrapage envoie plus d'une semaine à la fois.
            if latest is None or reading.period_end > latest.period_end:
                latest = reading

        # La fiche de licence porte ce que le fournisseur affirme, daté. On ne
        # touche à `seats_total` et à l'expiration que s'ils ont bougé, pour ne
        # pas noyer le suivi de la fiche à chaque relevé.
        if latest:
            lic = License.search([("saas_provider", "=", provider)], limit=1)
            if lic:
                lic_vals = {
                    "reported_seats_used": latest.seats_used,
                    "reported_seats_date": latest.period_end,
                }
                if latest.seats_total and lic.seats_total != latest.seats_total:
                    lic_vals["seats_total"] = latest.seats_total
                if latest.license_expiry and lic.expiry_date != latest.license_expiry:
                    lic_vals["expiry_date"] = latest.license_expiry
                lic.write(lic_vals)
            else:
                _logger.info(
                    "Relevé %s : aucune licence n'est rattachée au produit %s",
                    latest.period_end, provider,
                )

        return {"created": created, "updated": updated, "unmatched": unmatched}

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
