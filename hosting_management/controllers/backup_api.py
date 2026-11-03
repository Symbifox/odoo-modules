# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import hmac
import json
import logging
import re
from datetime import datetime, timezone

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from psycopg2 import IntegrityError
from psycopg2.errors import SerializationFailure

from odoo import fields, http
from markupsafe import Markup, escape
from odoo.http import request

from ..models.hosting_backup_log import _icp_truthy
from ..models.hosting_lang import reader_env

_logger = logging.getLogger(__name__)


def _host_timestamp_to_utc(env, value):
    """Lire l'horodatage d'un rapport, qui est l'heure LOCALE de l'hôte.

    🔴 `backup-all-services.restic.sh` pose son horodatage avec
    `date +'%Y-%m-%d %H:%M:%S'`, donc l'heure locale de l'hôte, sans
    fuseau. Rangée telle quelle, elle se lisait comme de l'UTC : les
    snapshots portaient 04:00:01 pendant que le watchdog, lui
    en ISO, datait les mêmes sauvegardes de 09:00 à 11:31 UTC. Cinq heures
    d'écart, donc une sauvegarde déclarée périmée cinq heures trop tôt,
    chaque jour, sur l'écran bâti pour qu'un rouge veuille dire quelque
    chose. Le fuseau vient de `hosting.backup_report_timezone`, que le cron
    du rapport quotidien lit déjà.
    """
    if not value:
        return False
    try:
        naive = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return False
    tz_name = (
        env["ir.config_parameter"]
        .sudo()
        .get_param("hosting.backup_report_timezone", "America/Toronto")
    )
    try:
        tz = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError):
        _logger.warning(
            "Backup report: unknown timezone %r, timestamp kept as is", tz_name
        )
        return naive
    return naive.replace(tzinfo=tz).astimezone(timezone.utc).replace(tzinfo=None)


# Borne de l'`integer` de PostgreSQL : au-delà, l'INSERT casse la transaction
# entière, et avec elle les 41 rafraîchissements de dépôts de la charge.
_INT4_MAX = 2147483647


def _as_int(value, default=0):
    try:
        nombre = int(float(value))
    except (TypeError, ValueError, OverflowError):
        return default
    return max(-_INT4_MAX, min(_INT4_MAX, nombre))


def _as_float(value, default=0.0):
    try:
        nombre = float(value)
    except (TypeError, ValueError):
        return default
    # `json.loads` rend `inf` pour 1e999, et un float non fini part en erreur
    # à l'écriture, hors du try du contrôleur.
    return nombre if nombre == nombre and abs(nombre) != float("inf") else default


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

    # Ceilings on discovery. Per payload, and on the standing backlog of
    # destinations nobody has acknowledged yet: a per-payload cap alone bounds
    # nothing, since nothing bounds the number of payloads.
    _MAX_DISCOVERED_PER_PAYLOAD = 25
    _MAX_DISCOVERED_BACKLOG = 60

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
        # ⚠️ `hmac.compare_digest` lève TypeError sur une chaîne non ASCII, et cet
        # appel est hors du try des routes : un en-tête `X-Backup-Token: é` rendait
        # un 500 avec trace là où la réponse juste est un 401. On compare donc des
        # octets, ce qui est de toute façon la forme que la fonction veut.
        try:
            jeton_octets = (token or "").encode("utf-8")
            attendu_octets = expected_token.encode("utf-8")
        except (AttributeError, UnicodeError):
            jeton_octets, attendu_octets = b"", b"x"
        if not token or not hmac.compare_digest(jeton_octets, attendu_octets):
            return (
                False,
                request.make_json_response(
                    {"success": False, "error": "Invalid API token"},
                    status=401,
                ),
            )
        return (True, None)

    def _validate_request_token(self):
        """Jeton propre aux demandes de sauvegarde.

        🔴 Pourquoi un SECOND jeton, et non celui des rapports. Le jeton
        `hosting.backup_api_token` sert la télémétrie : l'hôte raconte ce qu'il a
        fait. Les deux routes de demandes, elles, pilotent une machine à états.
        Avec un seul jeton, un porteur pouvait prendre les demandes d'un
        gestionnaire, ou les fermer en « terminée » en citant le numéro d'une
        vraie exécution nocturne, et donc peindre un service en vert sans qu'une
        sauvegarde ait tourné.

        Pas de repli sur l'autre jeton : un repli annulerait la séparation. Si le
        paramètre manque, les routes refusent et le disent.
        """
        token = request.httprequest.headers.get("X-Backup-Request-Token")
        attendu = (
            request.env["ir.config_parameter"]
            .sudo()
            .get_param("hosting.backup_request_token", "")
        )
        if not attendu:
            return (
                False,
                request.make_json_response(
                    {
                        "success": False,
                        "error": (
                            "Backup request token not configured "
                            "(hosting.backup_request_token)"
                        ),
                    },
                    status=500,
                ),
            )
        # Même précaution que pour l'autre jeton : comparer des octets, parce que
        # `compare_digest` lève sur une chaîne non ASCII, et cet appel est hors du
        # try des routes.
        try:
            jeton_octets = (token or "").encode("utf-8")
            attendu_octets = attendu.encode("utf-8")
        except (AttributeError, UnicodeError):
            jeton_octets, attendu_octets = b"", b"x"
        if not token or not hmac.compare_digest(jeton_octets, attendu_octets):
            return (
                False,
                request.make_json_response(
                    {"success": False, "error": "Invalid backup request token"},
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
            # Note stockée, lue par l'équipe d'hébergement : langue de la société.
            "notes": reader_env(request.env)._(
                "Received via API at %s", data.get("timestamp")),
        }

        run_date = _host_timestamp_to_utc(request.env, data.get("timestamp"))
        if run_date:
            run_vals["run_date"] = run_date

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

        run_date = _host_timestamp_to_utc(request.env, data.get("timestamp"))

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

        # Une execution demandee a la main ne doit pas voler le creneau du
        # rapport quotidien, qui ne retient que la plus recente par societe et par
        # hote. Un clic a 05 h 30 faisait partir, a 06 h 00, un rapport d'une seule
        # ligne, et repoussait d'une heure celui de la nuit, c'est-a-dire
        # l'artefact sur lequel on lit si les sauvegardes ont tourne.
        # ⚠️ ICI, dans la route restic : c'est la seule que l'agent appelle. Pose
        # dans la route ZIP d'origine, le drapeau restait faux, et le parcours de
        # bout en bout l'a dit en affichant « a la demande = False ».
        run_vals = {
            "hostname": hostname,
            "report_type": "restic",
            "on_demand": bool(_icp_truthy(data.get("on_demand"), default=False)),
            "notes": reader_env(request.env)._(
                "Received via API at %s", data.get("timestamp")),
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
            repos_payload = data.get("repos")
            if not isinstance(repos_payload, dict):
                repos_payload = {}
            updated = []
            unknown = []
            # A destination the registry does not know used to be dropped
            # here with a log line nobody reads, so a repository holding real
            # snapshots (514 MB of reverse-proxy keys a night, in one case)
            # stayed invisible. It is now registered, flagged as discovered
            # and counted on the dashboard: present, but not yet claiming to
            # cover any service.
            created = 0
            backlog = Repo.search_count([("discovered", "=", True)])
            for name, info in repos_payload.items():
                # The payload is token-authenticated, not trusted. Every value
                # read below is validated first: an exception thrown halfway
                # used to leave a created row behind and lose the whole push.
                name = name if isinstance(name, str) else ""
                name = name.strip()
                if not name or len(name) > 256 or not isinstance(info, dict):
                    unknown.append(str(name)[:64] or "(unnamed)")
                    continue
                vals = {
                    "snapshot_count": _as_int(info.get("snapshot_count")),
                    "latest_snapshot_date": _parse_iso8601(
                        info.get("latest_snapshot_iso")
                    ),
                    "size_bytes": _as_float(info.get("size_bytes")),
                    "last_watchdog_sync": collected_at,
                }
                # `active_test=False` : un dépôt archivé porte le même nom et
                # la contrainte d'unicité le refuserait à chaque pousse, pour
                # toujours. L'hôte qui sauvegarde encore un client sorti du
                # parc est justement le cas que ce lot doit rendre visible.
                repo = Repo.with_context(active_test=False).search(
                    [("name", "=", name)], limit=1
                )
                if repo and not repo.active:
                    _logger.warning(
                        "Watchdog: archived repository '%s' is still reported "
                        "by the host", name
                    )
                    unknown.append(name)
                if not repo:
                    over_cap = (
                        created >= self._MAX_DISCOVERED_PER_PAYLOAD
                        or backlog + created >= self._MAX_DISCOVERED_BACKLOG
                    )
                    if over_cap:
                        unknown.append(name)
                        _logger.warning(
                            "Watchdog: discovery cap reached, '%s' skipped",
                            name,
                        )
                        continue
                    try:
                        with request.env.cr.savepoint():
                            repo = Repo.create({
                                "name": name,
                                "discovered": True,
                                "discovered_date": collected_at,
                            })
                    except IntegrityError:
                        # Two pushes racing on the same new destination: the
                        # loser re-reads instead of failing the whole payload.
                        repo = Repo.with_context(active_test=False).search(
                            [("name", "=", name)], limit=1
                        )
                        if not repo:
                            unknown.append(name)
                            continue
                    else:
                        created += 1
                        unknown.append(name)
                        _logger.warning(
                            "Watchdog: unregistered repository '%s' discovered "
                            "and recorded", name
                        )
                repo.write(vals)
                updated.append(name)

            bucket = data.get("bucket")
            if not isinstance(bucket, dict):
                bucket = {}
            request.env["hosting.backup.bucket.snapshot"].sudo().create({
                "collected_at": collected_at,
                "host": str(host)[:128],
                "total_objects": _as_int(bucket.get("total_objects")),
                "total_bytes": _as_float(bucket.get("total_bytes")),
            })

            return request.make_json_response({
                "success": True,
                "repos_updated": len(updated),
                "repos_unknown": unknown,
                "repos_discovered": created,
            })
        except Exception:
            # Sans ce retour en arrière, les écritures faites avant l'exception
            # sont validées par la fermeture du curseur : la charge utile
            # laisserait un registre à moitié rempli derrière un 500.
            request.env.cr.rollback()
            _logger.exception("Error processing watchdog state")
            return request.make_json_response(
                {"success": False, "error": "Internal server error"},
                status=500,
            )

    # ── Demandes de sauvegarde ────────────────────────
    #
    # 🔴 CE QUE CES DEUX ROUTES NE FONT PAS : donner un ordre. Odoo rend un
    # identifiant de service, le nom de son conteneur et le logiciel. L'agent sur
    # l'hôte résout LUI-MÊME, dans une table en dur, quel script lancer et sur
    # quelle instance. Aucune chaîne venue d'ici n'entre dans une commande.
    #
    # Si un jour la réponse porte un nom de script, un chemin ou un argument, la
    # doctrine est rompue : une faille Odoo deviendrait une exécution de commande
    # sur la machine qui tient les mots de passe de tous les conteneurs.

    _MAX_REQUESTS_PER_CLAIM = 5

    @http.route(
        "/api/hosting/backup/requests/public",
        type="http",
        auth="public",
        methods=["POST"],
        csrf=False,
    )
    def claim_backup_requests(self, **kwargs):
        """L'agent de l'hôte vient chercher les demandes en attente et les prend.

        Prendre et rendre la liste dans le même appel est volontaire : si l'agent
        lisait sans prendre, deux agents (le parc a déjà deux machines) liraient la
        même demande et la joueraient deux fois.
        """
        ok, err = self._validate_request_token()
        if not ok:
            return err

        try:
            data = json.loads(request.httprequest.data or b"{}")
        except (json.JSONDecodeError, ValueError) as e:
            return request.make_json_response(
                {"success": False, "error": f"Invalid JSON: {e}"}, status=400,
            )
        if not isinstance(data, dict):
            data = {}
        agent_host = str(data.get("host") or "unknown")[:128]

        try:
            Demande = request.env["hosting.backup.request"].sudo()
            # Le plafond n'est pas cosmétique : une file qui s'est accumulée pendant
            # une panne de l'agent ne doit pas se vider d'un coup sur la machine.
            #
            # L'agent dit combien il peut en jouer dans ce passage, et Odoo borne sa
            # demande. Avant, Odoo en donnait cinq et l'agent n'en jouait que trois :
            # les deux autres, déjà marquées « prises », étaient rendues en échec
            # alors qu'elles étaient parfaitement recevables. Une demande perdue
            # pour une raison d'arithmétique interne, c'est un clic pour rien.
            #
            # ⚠️ `or` et non `_as_int(...) or defaut` sans garde : `max: 0` rendait 0,
            # donc faux, donc le repli servait CINQ demandes à un agent qui en
            # annonçait zéro. Un repli doit aller vers le prudent.
            # `max` ABSENT, c'est un agent d'avant ce contrat : il reçoit le
            # plafond du serveur, comme avant. `max` PRÉSENT mais nul ou négatif,
            # c'est une valeur qu'on ne peut pas honorer : le repli va vers le
            # prudent, une seule demande, et non vers les cinq du serveur.
            if data.get("max") is None:
                demande_max = self._MAX_REQUESTS_PER_CLAIM
            else:
                demande_max = _as_int(data.get("max"))
                if demande_max <= 0:
                    demande_max = 1
            plafond = max(1, min(demande_max, self._MAX_REQUESTS_PER_CLAIM))

            # 🔴 B3 : la prise doit être ATOMIQUE. Un `search` suivi d'un `write`
            # laisse deux agents voir la même demande `pending` en READ COMMITTED :
            # le second UPDATE attend la validation du premier puis se réapplique
            # sur l'id (le WHERE de l'ORM ne porte pas sur l'état) et réussit. Les
            # deux repartent avec la demande et jouent la sauvegarde en même temps
            # sur la même instance, ce que le garde « une seule demande vivante »
            # existe précisément pour empêcher. `FOR UPDATE SKIP LOCKED` fait que
            # le second agent passe à la suivante au lieu d'attendre.
            #
            # 🔴 B5 : et la demande doit être pour CETTE machine. Le parc en a deux
            # (une seconde machine porte un service de secours). Sans ce filtre, le premier agent
            # qui sonde prend des demandes pour des conteneurs absents de chez lui :
            # le script sort alors en 0 sans instantané, Odoo écrit « échouée » sur
            # une demande parfaitement saine, et le vrai hôte ne la voit jamais.
            # La comparaison porte sur la PREMIÈRE étiquette du nom : la base dit
            # « machine.exemple.com » et la machine répond « machine ».
            court = agent_host.split(".")[0].lower()
            request.env.cr.execute(
                """
                SELECT r.id
                  FROM hosting_backup_request r
                  JOIN hosting_service s ON s.id = r.service_id
                  LEFT JOIN hosting_server v ON v.id = s.server_id
                 WHERE r.state = 'pending'
                   AND (
                        v.hostname IS NULL
                     OR lower(split_part(v.hostname, '.', 1)) = %s
                   )
                 ORDER BY r.requested_date ASC, r.id ASC
                 LIMIT %s
                   FOR UPDATE OF r SKIP LOCKED
                """,
                (court, plafond),
            )
            ids = [ligne[0] for ligne in request.env.cr.fetchall()]

            # ⚠️ Reprise. Si la requete a ete validee mais que la reponse n'est
            # pas arrivee (delai HTTP depasse), les demandes sont « prises » et
            # personne ne les joue : l'agent repart et ne redemande jamais ce
            # qu'il croit ne pas avoir. Le service restait alors bloque jusqu'au
            # delai des demandes bloquees, et Odoo ecrivait « l'agent n'a jamais
            # rendu compte » au sujet d'une passe qui n'avait pas commence. On
            # rend donc d'abord a cet hote ce qu'il a deja pris et jamais rendu.
            siennes = Demande.search(
                [("state", "=", "claimed"), ("agent_host", "=", agent_host)],
                order="claimed_date asc, id asc", limit=plafond,
            )
            ids = siennes.ids + [i for i in ids if i not in siennes.ids]
            attente = Demande.browse(ids[:plafond])
            prises = []
            maintenant = fields.Datetime.now()
            for demande in attente:
                service = demande.service_id
                # Le service a pu changer d'avis entre la demande et maintenant.
                if (
                    service.state not in ("active", "to_disconnect")
                    or service.backup_expectation != "pipeline"
                    or not service.docker_container
                ):
                    demande.write({
                        "state": "cancelled",
                        "finished_date": maintenant,
                        "message": (
                            "The service no longer qualifies for a pipeline backup."
                        ),
                    })
                    continue
                demande.write({
                    "state": "claimed",
                    "claimed_date": maintenant,
                    "agent_host": agent_host,
                })
                prises.append({
                    "id": demande.id,
                    "reference": demande.name,
                    "service_code": service.code or "",
                    "service_name": service.name or "",
                    "container": service.docker_container or "",
                    "software": (service.software_id.name or ""),
                })
            return request.make_json_response({
                "success": True,
                "host": agent_host,
                "requests": prises,
            })
        except SerializationFailure:
            # 🔴 NE PAS avaler : Odoo rejoue tout seul une requete qui echoue sur
            # la serialisation (`retrying()`), et c'est exactement ce qu'il faut
            # ici. Servie en 500, cette erreur desarmait ce rejeu et l'agent
            # journalisait « Odoo injoignable », ce qui est indechiffrable. Odoo
            # ouvre ses curseurs en REPEATABLE READ : le cron d'expiration, une
            # annulation ou un autre agent suffisent a la declencher.
            raise
        except Exception:
            request.env.cr.rollback()
            _logger.exception("Error claiming backup requests")
            return request.make_json_response(
                {"success": False, "error": "Internal server error"}, status=500,
            )

    @http.route(
        "/api/hosting/backup/requests/report/public",
        type="http",
        auth="public",
        methods=["POST"],
        csrf=False,
    )
    def report_backup_request(self, **kwargs):
        """L'agent rend compte d'une demande qu'il a prise."""
        ok, err = self._validate_request_token()
        if not ok:
            return err

        try:
            data = json.loads(request.httprequest.data or b"{}")
        except (json.JSONDecodeError, ValueError) as e:
            return request.make_json_response(
                {"success": False, "error": f"Invalid JSON: {e}"}, status=400,
            )
        if not isinstance(data, dict):
            data = {}

        demande_id = _as_int(data.get("id"))
        etat = data.get("state")
        if etat not in ("running", "done", "failed"):
            return request.make_json_response(
                {"success": False, "error": "state must be running, done or failed"},
                status=400,
            )
        try:
            Demande = request.env["hosting.backup.request"].sudo()
            demande = Demande.browse(demande_id).exists()
            if not demande:
                return request.make_json_response(
                    {"success": False, "error": "Unknown request"}, status=404,
                )
            # Une demande que l'agent n'a pas prise ne lui appartient pas : sans ce
            # contrôle, un appel pourrait clore une demande qu'un autre agent joue.
            # Un « running » sur une demande déjà « running » n'apporte rien et
            # faisait grossir le chatter à chaque appel : une boucle suffisait.
            if etat == "running" and demande.state == "running":
                return request.make_json_response(
                    {"success": True, "reference": demande.name, "state": etat,
                     "note": "already running"},
                )
            if demande.state not in ("claimed", "running"):
                return request.make_json_response(
                    {
                        "success": False,
                        "error": f"Request {demande.name} is {demande.state}",
                    },
                    status=409,
                )
            # 🔴 B4 : le compte rendu doit venir de l'agent QUI A PRIS la demande.
            # Le commentaire d'origine promettait ce contrôle, le code ne regardait
            # que l'état : n'importe quel porteur du jeton pouvait fermer en « done »
            # une demande qu'un agent jouait encore, et le vrai compte rendu
            # arrivait ensuite sur un 409, donc était perdu. On pouvait ainsi faire
            # écrire « sauvegarde terminée » sur une sauvegarde qui n'a pas tourné.
            # ⚠️ Comparer la PREMIERE etiquette, comme la prise : un agent qui
            # prend en « machine » et rend en « machine.exemple.com »
            # prenait un 409, la demande restait prise, et le cron la fermait en
            # echec deux heures plus tard.
            dit_par = str(data.get("host") or "")[:128]
            court_dit = dit_par.split(".")[0].lower()
            court_pris = (demande.agent_host or "").split(".")[0].lower()
            # 🔴 Et l'omission n'est pas une permission : sans `host`, l'appelant
            # passait la garde en entier et pouvait fermer en « done » une demande
            # qu'un autre hote jouait encore.
            if not dit_par:
                return request.make_json_response(
                    {
                        "success": False,
                        "error": "host is required: say which agent is reporting",
                    },
                    status=400,
                )
            if court_pris and court_dit != court_pris:
                return request.make_json_response(
                    {
                        "success": False,
                        "error": (
                            f"{demande.name} was taken by {demande.agent_host}, "
                            f"not by {dit_par}"
                        ),
                    },
                    status=409,
                )

            # ⚠️ Le message n'est pas forcément une chaîne : un dict faisait lever
            # le découpage, donc un 500 à volonté pour un appelant authentifié.
            brut = data.get("message")
            if not isinstance(brut, str):
                brut = "" if brut is None else str(brut)
            vals = {"state": etat, "message": brut[:4000]}
            # C6 : « running » repose le chrono. Sans cela, une demande prise avec
            # quatre autres voyait son délai courir pendant qu'elle attendait son
            # tour dans la file locale de l'agent, et le cron la fermait en échec
            # avant même qu'elle démarre.
            if etat == "running":
                vals["claimed_date"] = fields.Datetime.now()
            if etat in ("done", "failed"):
                vals["finished_date"] = fields.Datetime.now()
            # C3 : rattacher l'exécution que l'agent vient de verser, pour que la
            # fiche du service passe au vert tout de suite.
            run_id = _as_int(data.get("run_id"))
            if run_id and request.env["hosting.backup.run"].sudo().browse(run_id).exists():
                vals["run_id"] = run_id
            demande.write(vals)
            # 🔴 Le corps d'un message de chatter est du HTML, et ce texte vient de
            # l'hote par une route a jeton. Insere tel quel, un `<` casse
            # l'affichage et une balise s'execute chez le lecteur. Le compte rendu
            # est donc echappe, et les sauts de ligne rendus en <br/> plutot que
            # perdus. Meme famille que le HTML echappe du chatter, pris
            # par l'autre bout.
            corps = escape(
                f"Agent report ({demande.agent_host or 'unknown'}): {etat}. "
                f"{brut[:2000]}"
            ).replace("\n", Markup("<br/>"))
            demande.message_post(body=corps, subtype_xmlid="mail.mt_note")
            return request.make_json_response({
                "success": True, "reference": demande.name, "state": etat,
            })
        except SerializationFailure:
            raise          # voir claim_backup_requests : le rejeu d'Odoo doit jouer
        except Exception:
            request.env.cr.rollback()
            _logger.exception("Error reporting a backup request")
            return request.make_json_response(
                {"success": False, "error": "Internal server error"}, status=500,
            )
