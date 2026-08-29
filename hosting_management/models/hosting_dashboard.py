# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import logging
from datetime import timedelta

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


def _human_bytes(n):
    if not n:
        return "0 B"
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if n < 1024.0:
            return f"{n:.2f} {unit}" if unit != "B" else f"{int(n)} {unit}"
        n /= 1024.0
    return f"{n:.2f} EB"


class HostingDashboard(models.AbstractModel):
    _name = "hosting.dashboard"
    _description = "Tableau de bord d'hébergement"
    # `AbstractModel` : ce modèle n'a ni champ ni table, il ne sert que de point
    # d'entrée RPC pour le composant OWL. Déclaré `models.Model` + `_auto = False`,
    # il entrait dans `Registry.check_tables_exist()`, qui ne dispense que
    # `_abstract` et les modèles à `_table_query` — d'où un `ERROR
    # odoo.modules.registry: Model <ce modèle> has no table.` journalisé à chaque
    # passe du chargeur sur une base neuve.

    # ------------------------------------------------------------------
    # Public API — called from OWL client action
    # ------------------------------------------------------------------

    @api.model
    def get_dashboard_data(self):
        """Return all dashboard data as a single dict for the OWL component."""
        services_flat = self._get_uptime_by_service()
        return {
            "uptime_overview": self._get_uptime_overview(),
            "uptime_by_service": services_flat,
            "uptime_by_software": self._group_by_software(services_flat),
            "alerts": self._get_alerts(),
            "expiring": self._get_expiring(),
            "maintenance": self._get_maintenance(),
            "backups": self._get_backups(),
            "services": self._get_services(),
            "domains": self._get_domains(),
            "security": self._get_security(),
        }

    # ------------------------------------------------------------------
    # Action methods — called via orm.call from the OWL component
    # ------------------------------------------------------------------

    @api.model
    def action_refresh_all(self):
        """Run health checks + version checks + Docker checks, return summary."""
        self.env["hosting.audit.log"]._log_event(
            action_type="health_check",
            category="ops",
            description="Actualisation complète du tableau de bord (santé + versions + Docker)",
            res_model=self._name,
        )
        health_check_count = 0
        health_check_errors = []
        version_check_count = 0
        version_check_errors = []

        Service = self.env["hosting.service"]
        all_services = Service.search([])

        # 1. Health checks
        services_to_check = Service.search([
            ("state", "=", "active"),
            ("server_url", "!=", False),
        ])

        try:
            import requests
            has_requests = True
        except ImportError:
            has_requests = False
            health_check_errors.append("requests library not installed")

        if has_requests:
            for service in services_to_check:
                try:
                    response = requests.get(
                        service.server_url,
                        timeout=10,
                        allow_redirects=True,
                        headers={"User-Agent": "Odoo-Hosting-Health-Check/1.0"},
                    )
                    status = "up" if response.status_code < 400 else "degraded"
                    self.env["hosting.health.check"].create({
                        "service_id": service.id,
                        "status": status,
                        "response_time_ms": int(
                            response.elapsed.total_seconds() * 1000
                        ),
                        "http_status_code": response.status_code,
                    })
                    health_check_count += 1
                except requests.Timeout:
                    self.env["hosting.health.check"].create({
                        "service_id": service.id,
                        "status": "timeout",
                        "error_message": "Request timed out after 10 seconds",
                    })
                    health_check_count += 1
                except requests.RequestException as e:
                    self.env["hosting.health.check"].create({
                        "service_id": service.id,
                        "status": "down",
                        "error_message": str(e)[:500],
                    })
                    health_check_count += 1
                    health_check_errors.append(
                        f"{service.name}: {str(e)[:100]}"
                    )
                except Exception as e:
                    _logger.exception(
                        "Error checking health for service %s", service.name
                    )
                    health_check_errors.append(
                        f"{service.name}: {str(e)[:100]}"
                    )

        # 2. Version checks
        Software = self.env["hosting.software"]
        software_to_check = Software.search([
            ("version_check_method", "!=", "none"),
        ])

        for software in software_to_check:
            try:
                software._check_version()
                version_check_count += 1
            except Exception as e:
                _logger.exception("Version check failed for %s", software.name)
                version_check_errors.append(
                    f"{software.name}: {str(e)[:100]}"
                )

        # 3. Docker container versions
        docker_check_count = 0
        docker_check_errors = []
        try:
            docker_results = Service._check_docker_versions()
            docker_check_count = docker_results.get("checked", 0)
            docker_check_errors = docker_results.get("errors", [])
        except Exception as e:
            _logger.exception("Docker version check failed")
            docker_check_errors.append(f"Docker check error: {str(e)[:100]}")

        # 4. Recompute stored fields
        if all_services:
            all_services.invalidate_recordset(
                ["update_available", "storage_alert", "storage_used_percent"]
            )
            for service in all_services:
                _ = service.update_available
                _ = service.storage_alert

        all_errors = (
            health_check_errors + version_check_errors + docker_check_errors
        )
        return {
            "health_check_count": health_check_count,
            "version_check_count": version_check_count,
            "docker_check_count": docker_check_count,
            "errors": all_errors,
        }

    @api.model
    def action_run_health_checks(self):
        """Run health checks on all active services with URLs."""
        self.env["hosting.audit.log"]._log_event(
            action_type="health_check",
            category="ops",
            description="Vérification de santé manuelle lancée depuis le tableau de bord",
            res_model=self._name,
        )
        Service = self.env["hosting.service"]
        services_to_check = Service.search([
            ("state", "=", "active"),
            ("server_url", "!=", False),
        ])

        if not services_to_check:
            return {"checked": 0, "up": 0, "down": 0}

        try:
            import requests
        except ImportError:
            return {"error": "requests library not installed"}

        checked = 0
        up_count = 0
        down_count = 0

        for service in services_to_check:
            try:
                response = requests.get(
                    service.server_url,
                    timeout=10,
                    allow_redirects=True,
                    headers={"User-Agent": "Odoo-Hosting-Health-Check/1.0"},
                )
                status = "up" if response.status_code < 400 else "degraded"
                self.env["hosting.health.check"].create({
                    "service_id": service.id,
                    "status": status,
                    "response_time_ms": int(
                        response.elapsed.total_seconds() * 1000
                    ),
                    "http_status_code": response.status_code,
                })
                if status == "up":
                    up_count += 1
                else:
                    down_count += 1
                checked += 1
            except requests.Timeout:
                self.env["hosting.health.check"].create({
                    "service_id": service.id,
                    "status": "timeout",
                    "error_message": "Request timed out after 10 seconds",
                })
                down_count += 1
                checked += 1
            except Exception as e:
                self.env["hosting.health.check"].create({
                    "service_id": service.id,
                    "status": "down",
                    "error_message": str(e)[:500],
                })
                down_count += 1
                checked += 1

        return {"checked": checked, "up": up_count, "down": down_count}

    @api.model
    def action_check_versions(self):
        """Check for new versions on all software with auto-check enabled."""
        Software = self.env["hosting.software"]
        software_to_check = Software.search([
            ("version_check_method", "!=", "none"),
        ])

        if not software_to_check:
            return {"checked": 0, "errors": []}

        checked = 0
        errors = []

        for software in software_to_check:
            try:
                software._check_version()
                checked += 1
            except Exception as e:
                errors.append(f"{software.name}: {str(e)[:50]}")

        return {"checked": checked, "errors": errors}

    # ------------------------------------------------------------------
    # Navigation actions — return act_window dicts
    # ------------------------------------------------------------------

    @api.model
    def action_view_expiring_30(self):
        today = fields.Date.today()
        return {
            "type": "ir.actions.act_window",
            "name": "Expirant dans 30 jours",
            "res_model": "hosting.service",
            "views": [[False, "list"], [False, "form"], [False, "kanban"]],
            "domain": [
                ("state", "=", "active"),
                ("date_expiration", ">", str(today)),
                ("date_expiration", "<=", str(today + timedelta(days=30))),
            ],
        }

    @api.model
    def action_view_expiring_60(self):
        today = fields.Date.today()
        return {
            "type": "ir.actions.act_window",
            "name": "Expirant dans 60 jours",
            "res_model": "hosting.service",
            "views": [[False, "list"], [False, "form"], [False, "kanban"]],
            "domain": [
                ("state", "=", "active"),
                ("date_expiration", ">", str(today)),
                ("date_expiration", "<=", str(today + timedelta(days=60))),
            ],
        }

    @api.model
    def action_view_expiring_90(self):
        today = fields.Date.today()
        return {
            "type": "ir.actions.act_window",
            "name": "Expirant dans 90 jours",
            "res_model": "hosting.service",
            "views": [[False, "list"], [False, "form"], [False, "kanban"]],
            "domain": [
                ("state", "=", "active"),
                ("date_expiration", ">", str(today)),
                ("date_expiration", "<=", str(today + timedelta(days=90))),
            ],
        }

    @api.model
    def action_view_updates(self):
        return {
            "type": "ir.actions.act_window",
            "name": "Mises à jour disponibles",
            "res_model": "hosting.service",
            "views": [[False, "list"], [False, "form"], [False, "kanban"]],
            "domain": [
                ("state", "=", "active"),
                ("update_available", "=", True),
            ],
        }

    @api.model
    def action_view_storage_alerts(self):
        return {
            "type": "ir.actions.act_window",
            "name": "Alertes de stockage",
            "res_model": "hosting.service",
            "views": [[False, "list"], [False, "form"], [False, "kanban"]],
            "domain": [
                ("state", "=", "active"),
                ("storage_alert", "=", True),
            ],
        }

    @api.model
    def action_view_health_issues(self):
        return {
            "type": "ir.actions.act_window",
            "name": "Problèmes de santé",
            "res_model": "hosting.service",
            "views": [[False, "list"], [False, "form"], [False, "kanban"]],
            "domain": [
                ("state", "=", "active"),
                ("is_service_up", "=", False),
            ],
        }

    @api.model
    def action_view_active_services(self):
        return {
            "type": "ir.actions.act_window",
            "name": "Services actifs",
            "res_model": "hosting.service",
            "views": [[False, "list"], [False, "form"], [False, "kanban"]],
            "domain": [("state", "=", "active")],
        }

    @api.model
    def action_view_suspended_services(self):
        return {
            "type": "ir.actions.act_window",
            "name": "Services suspendus",
            "res_model": "hosting.service",
            "views": [[False, "list"], [False, "form"], [False, "kanban"]],
            "domain": [("state", "=", "suspended")],
        }

    @api.model
    def action_view_expired_services(self):
        return {
            "type": "ir.actions.act_window",
            "name": "Services expirés",
            "res_model": "hosting.service",
            "views": [[False, "list"], [False, "form"], [False, "kanban"]],
            "domain": [("state", "=", "expired")],
        }

    @api.model
    def action_view_maintenance_overdue(self):
        today = fields.Date.today()
        return {
            "type": "ir.actions.act_window",
            "name": "Maintenance en retard",
            "res_model": "hosting.maintenance.schedule",
            "views": [[False, "list"], [False, "form"]],
            "domain": [
                ("active", "=", True),
                ("next_due", "<", str(today)),
                ("service_id.state", "=", "active"),
            ],
            "context": {"search_default_group_by_service": 1},
        }

    @api.model
    def action_view_maintenance_due_week(self):
        today = fields.Date.today()
        return {
            "type": "ir.actions.act_window",
            "name": "Prévue cette semaine",
            "res_model": "hosting.maintenance.schedule",
            "views": [[False, "list"], [False, "form"]],
            "domain": [
                ("active", "=", True),
                ("next_due", ">=", str(today)),
                ("next_due", "<=", str(today + timedelta(days=7))),
                ("service_id.state", "=", "active"),
            ],
            "context": {"search_default_group_by_service": 1},
        }

    @api.model
    def action_view_maintenance_due_month(self):
        today = fields.Date.today()
        return {
            "type": "ir.actions.act_window",
            "name": "Prévue ce mois-ci",
            "res_model": "hosting.maintenance.schedule",
            "views": [[False, "list"], [False, "form"]],
            "domain": [
                ("active", "=", True),
                ("next_due", ">=", str(today)),
                ("next_due", "<=", str(today + timedelta(days=30))),
                ("service_id.state", "=", "active"),
            ],
            "context": {"search_default_group_by_service": 1},
        }

    @api.model
    def action_view_backup_runs(self):
        return {
            "type": "ir.actions.act_window",
            "name": "Exécutions de sauvegarde",
            "res_model": "hosting.backup.run",
            "views": [[False, "list"], [False, "form"]],
            "context": {"search_default_filter_month": 1},
        }

    @api.model
    def action_view_failed_backups(self):
        return {
            "type": "ir.actions.act_window",
            "name": "Sauvegardes échouées",
            "res_model": "hosting.backup.run",
            "views": [[False, "list"], [False, "form"]],
            "domain": [("state", "in", ["failed", "partial"])],
        }

    @api.model
    def action_view_restic_repositories(self):
        return {
            "type": "ir.actions.act_window",
            "name": "Dépôts Restic",
            "res_model": "hosting.backup.repository",
            "views": [[False, "list"], [False, "form"]],
            "context": {"search_default_group_category": 1},
        }

    @api.model
    def action_view_restic_snapshots(self):
        return {
            "type": "ir.actions.act_window",
            "name": "Snapshots Restic (24h)",
            "res_model": "hosting.backup.snapshot",
            "views": [[False, "list"]],
            "domain": [
                (
                    "snapshot_date",
                    ">=",
                    fields.Datetime.to_string(
                        fields.Datetime.subtract(fields.Datetime.now(), hours=24)
                    ),
                )
            ],
        }

    @api.model
    def action_view_health_checks(self):
        return {
            "type": "ir.actions.act_window",
            "name": "Vérifications de santé",
            "res_model": "hosting.health.check",
            "views": [[False, "list"], [False, "form"]],
        }

    # ------------------------------------------------------------------
    # Private helpers — build each dashboard section
    # ------------------------------------------------------------------

    @api.model
    def _get_uptime_overview(self):
        """Fleet-wide uptime %, avg response time, services down."""
        self.env.cr.execute("""
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE status = 'up') AS up_count,
                ROUND(AVG(response_time_ms) FILTER (
                    WHERE status = 'up' AND response_time_ms > 0
                ))::int AS avg_ms
            FROM hosting_health_check
            WHERE check_date >= NOW() - INTERVAL '24 hours'
              AND NOT excluded_from_stats
        """)
        row = self.env.cr.dictfetchone()
        total = row["total"] or 0
        up_count = row["up_count"] or 0
        uptime_pct = round((up_count / total) * 100, 1) if total else 0.0
        avg_response_ms = row["avg_ms"] or 0

        # Current down count via lateral join (one query for all services).
        # Excluded checks are skipped so an ignored external incident doesn't
        # count a service as down.
        self.env.cr.execute("""
            SELECT COUNT(*) FROM hosting_service hs
            CROSS JOIN LATERAL (
                SELECT status FROM hosting_health_check hc
                WHERE hc.service_id = hs.id
                  AND NOT hc.excluded_from_stats
                ORDER BY hc.check_date DESC LIMIT 1
            ) latest
            WHERE hs.state = 'active'
              AND hs.server_url IS NOT NULL AND hs.server_url != ''
              AND latest.status != 'up'
        """)
        down_count = self.env.cr.fetchone()[0] or 0

        self.env.cr.execute("""
            SELECT COUNT(*) FROM hosting_service
            WHERE state = 'active'
              AND server_url IS NOT NULL AND server_url != ''
        """)
        monitored = self.env.cr.fetchone()[0] or 0

        return {
            "uptime_pct": uptime_pct,
            "avg_response_ms": avg_response_ms,
            "down_count": down_count,
            "total_checks_24h": total,
            "monitored_services": monitored,
        }

    @api.model
    def _get_uptime_by_service(self):
        """Per-service uptime for 24h/7d/30d windows — single SQL query."""
        lang = self.env.lang or "en_US"
        self.env.cr.execute("""
            WITH service_ids AS (
                SELECT id FROM hosting_service
                WHERE state = 'active'
                  AND server_url IS NOT NULL AND server_url != ''
            ),
            latest_check AS (
                SELECT DISTINCT ON (service_id)
                    service_id, status, check_date
                FROM hosting_health_check
                WHERE service_id IN (SELECT id FROM service_ids)
                  AND NOT excluded_from_stats
                ORDER BY service_id, check_date DESC
            ),
            agg AS (
                SELECT
                    hc.service_id,
                    -- 24h
                    COUNT(*) FILTER (
                        WHERE hc.check_date >= NOW() - INTERVAL '24 hours'
                    ) AS total_24h,
                    COUNT(*) FILTER (
                        WHERE hc.check_date >= NOW() - INTERVAL '24 hours'
                          AND hc.status = 'up'
                    ) AS up_24h,
                    -- 7d
                    COUNT(*) FILTER (
                        WHERE hc.check_date >= NOW() - INTERVAL '7 days'
                    ) AS total_7d,
                    COUNT(*) FILTER (
                        WHERE hc.check_date >= NOW() - INTERVAL '7 days'
                          AND hc.status = 'up'
                    ) AS up_7d,
                    -- 30d
                    COUNT(*) AS total_30d,
                    COUNT(*) FILTER (WHERE hc.status = 'up') AS up_30d,
                    -- avg response
                    ROUND(AVG(hc.response_time_ms) FILTER (
                        WHERE hc.status = 'up' AND hc.response_time_ms > 0
                    ))::int AS avg_ms
                FROM hosting_health_check hc
                WHERE hc.service_id IN (SELECT id FROM service_ids)
                  AND hc.check_date >= NOW() - INTERVAL '30 days'
                  AND NOT hc.excluded_from_stats
                GROUP BY hc.service_id
            )
            SELECT
                hs.id AS service_id,
                hs.name AS service_name,
                COALESCE(hs.code, '') AS service_code,
                COALESCE(rp.name, '') AS partner_name,
                COALESCE(hs.server_url, '') AS server_url,
                hs.software_id AS software_id,
                COALESCE(sw.name->>%(lang)s, sw.name->>'en_US', '') AS software_name,
                COALESCE(lc.status, 'unknown') AS current_status,
                lc.check_date AS last_check,
                COALESCE(a.total_24h, 0) AS total_24h,
                COALESCE(a.up_24h, 0) AS up_24h,
                COALESCE(a.total_7d, 0) AS total_7d,
                COALESCE(a.up_7d, 0) AS up_7d,
                COALESCE(a.total_30d, 0) AS total_30d,
                COALESCE(a.up_30d, 0) AS up_30d,
                COALESCE(a.avg_ms, 0) AS avg_response_ms,
                COALESCE(hs.storage_used_gb, 0) AS storage_used_gb,
                COALESCE(hs.storage_quota_gb, 0) AS storage_quota_gb,
                COALESCE(hs.storage_used_percent, 0) AS storage_used_percent
            FROM hosting_service hs
            LEFT JOIN res_partner rp ON rp.id = hs.partner_id
            LEFT JOIN hosting_software sw ON sw.id = hs.software_id
            LEFT JOIN latest_check lc ON lc.service_id = hs.id
            LEFT JOIN agg a ON a.service_id = hs.id
            WHERE hs.state = 'active'
              AND hs.server_url IS NOT NULL AND hs.server_url != ''
            ORDER BY
                CASE COALESCE(lc.status, 'unknown')
                    WHEN 'down' THEN 0
                    WHEN 'timeout' THEN 1
                    WHEN 'degraded' THEN 2
                    WHEN 'up' THEN 3
                    ELSE 4
                END,
                hs.name
        """, {"lang": lang})
        rows = self.env.cr.dictfetchall()

        results = []
        for r in rows:
            t24 = r["total_24h"]
            t7 = r["total_7d"]
            t30 = r["total_30d"]
            results.append({
                "service_id": r["service_id"],
                "service_name": r["service_name"],
                "service_code": r["service_code"],
                "partner_name": r["partner_name"],
                "server_url": r["server_url"],
                "software_id": r["software_id"] or 0,
                "software_name": r["software_name"] or "(Sans logiciel)",
                "current_status": r["current_status"],
                "last_check": (
                    fields.Datetime.to_string(r["last_check"])
                    if r["last_check"] else ""
                ),
                "uptime_24h": (
                    round((r["up_24h"] / t24) * 100, 1) if t24 else 0.0
                ),
                "uptime_7d": (
                    round((r["up_7d"] / t7) * 100, 1) if t7 else 0.0
                ),
                "uptime_30d": (
                    round((r["up_30d"] / t30) * 100, 1) if t30 else 0.0
                ),
                "avg_response_ms": r["avg_response_ms"],
                "total_checks_30d": t30,
                "storage_used_gb": round(r["storage_used_gb"], 2),
                "storage_quota_gb": round(r["storage_quota_gb"], 2),
                "storage_used_percent": round(r["storage_used_percent"], 1),
            })
        return results

    @api.model
    def _group_by_software(self, services):
        """Aggregate per-service rows into per-software groups.

        Each group exposes a summary (counts, worst status, slowest avg,
        worst uptime/storage) plus a `has_issues` flag the frontend uses to
        auto-expand the group. Non-up status, sub-99.5% 24h uptime,
        storage ≥75%, or avg response above the configured threshold each
        flag the group as worthy of mention.
        """
        threshold = int(
            self.env["ir.config_parameter"].sudo()
            .get_param("hosting.response_time_threshold_ms", "5000")
        )
        status_rank = {
            "down": 0, "timeout": 1, "degraded": 2,
            "unknown": 3, "up": 4,
        }
        groups = {}
        for svc in services:
            key = svc["software_id"]
            g = groups.get(key)
            if not g:
                g = {
                    "software_id": key,
                    "software_name": svc["software_name"],
                    "service_count": 0,
                    "up_count": 0,
                    "degraded_count": 0,
                    "down_count": 0,
                    "timeout_count": 0,
                    "unknown_count": 0,
                    "worst_status": "up",
                    "min_uptime_24h": 100.0,
                    "max_avg_response_ms": 0,
                    "max_storage_pct": 0.0,
                    "has_issues": False,
                    "services": [],
                }
                groups[key] = g
            g["service_count"] += 1
            g["services"].append(svc)
            status = svc["current_status"]
            g[f"{status}_count" if status in ("up", "degraded", "down", "timeout", "unknown") else "unknown_count"] += 1
            if status_rank.get(status, 5) < status_rank.get(g["worst_status"], 5):
                g["worst_status"] = status
            if svc["uptime_24h"] < g["min_uptime_24h"]:
                g["min_uptime_24h"] = svc["uptime_24h"]
            if svc["avg_response_ms"] > g["max_avg_response_ms"]:
                g["max_avg_response_ms"] = svc["avg_response_ms"]
            if svc["storage_used_percent"] > g["max_storage_pct"]:
                g["max_storage_pct"] = svc["storage_used_percent"]

        for g in groups.values():
            g["has_issues"] = (
                g["worst_status"] != "up"
                or g["min_uptime_24h"] < 99.5
                or g["max_storage_pct"] >= 75.0
                or g["max_avg_response_ms"] > threshold
            )
            g["min_uptime_24h"] = round(g["min_uptime_24h"], 1)
            g["max_storage_pct"] = round(g["max_storage_pct"], 1)

        # Sort: issues first (by worst status), then by software name
        return sorted(
            groups.values(),
            key=lambda g: (
                0 if g["has_issues"] else 1,
                status_rank.get(g["worst_status"], 5),
                g["software_name"].lower(),
            ),
        )

    @api.model
    def _get_alerts(self):
        """Updates available, storage alerts, health issues."""
        Service = self.env["hosting.service"]
        active_domain = [("state", "=", "active")]
        return {
            "updates_available": Service.search_count(
                active_domain + [("update_available", "=", True)]
            ),
            "storage_alerts": Service.search_count(
                active_domain + [("storage_alert", "=", True)]
            ),
            "health_issues": Service.search_count(
                active_domain + [("is_service_up", "=", False)]
            ),
        }

    @api.model
    def _get_expiring(self):
        """Services expiring in 30/60/90 days."""
        today = fields.Date.today()
        Service = self.env["hosting.service"]
        base = [("state", "=", "active"), ("date_expiration", ">", today)]
        return {
            "expiring_30": Service.search_count(
                base + [("date_expiration", "<=", today + timedelta(days=30))]
            ),
            "expiring_60": Service.search_count(
                base + [("date_expiration", "<=", today + timedelta(days=60))]
            ),
            "expiring_90": Service.search_count(
                base + [("date_expiration", "<=", today + timedelta(days=90))]
            ),
        }

    @api.model
    def _get_maintenance(self):
        """Overdue, due this week, due this month."""
        today = fields.Date.today()
        MaintenanceSchedule = self.env["hosting.maintenance.schedule"]

        return {
            "overdue": MaintenanceSchedule.search_count([
                ("active", "=", True),
                ("next_due", "<", today),
                ("service_id.state", "=", "active"),
            ]),
            "due_week": MaintenanceSchedule.search_count([
                ("active", "=", True),
                ("next_due", ">=", today),
                ("next_due", "<=", today + timedelta(days=7)),
                ("service_id.state", "=", "active"),
            ]),
            "due_month": MaintenanceSchedule.search_count([
                ("active", "=", True),
                ("next_due", ">=", today),
                ("next_due", "<=", today + timedelta(days=30)),
                ("service_id.state", "=", "active"),
            ]),
        }

    @api.model
    def _get_backups(self):
        """Backup run statistics (legacy keys + Restic extension)."""
        BackupRun = self.env["hosting.backup.run"]
        last_run = BackupRun.search([], order="run_date desc", limit=1)

        today = fields.Date.today()
        first_of_month = today.replace(day=1)
        runs_this_month = BackupRun.search_count([
            ("run_date", ">=", first_of_month),
        ])

        # Backward-compat keys for the existing JS dashboard
        if last_run:
            base = {
                "last_run_date": fields.Datetime.to_string(last_run.run_date),
                "last_run_state": last_run.state,
                "success_count": last_run.success_count,
                "failed_count": last_run.failed_count,
                "runs_this_month": runs_this_month,
            }
        else:
            base = {
                "last_run_date": False,
                "last_run_state": False,
                "success_count": 0,
                "failed_count": 0,
                "runs_this_month": runs_this_month,
            }

        # Restic extension
        Repo = self.env["hosting.backup.repository"]
        Snapshot = self.env["hosting.backup.snapshot"]
        BucketSnap = self.env["hosting.backup.bucket.snapshot"]

        active_repos = Repo.search([("active", "=", True)])
        stale_repos = active_repos.filtered(lambda r: r.is_stale)
        total_size = sum(active_repos.mapped("size_bytes"))
        last_restic = BackupRun.search(
            [("report_type", "=", "restic")], order="run_date desc", limit=1
        )
        snapshots_24h = Snapshot.search_count([
            (
                "snapshot_date",
                ">=",
                fields.Datetime.subtract(fields.Datetime.now(), hours=24),
            )
        ])
        latest_bucket = BucketSnap.search([], order="collected_at desc", limit=1)

        base.update({
            "restic_repo_count": len(active_repos),
            "restic_stale_count": len(stale_repos),
            "restic_total_size_human": _human_bytes(total_size),
            "restic_last_run_state": last_restic.state if last_restic else False,
            "restic_last_run_date": (
                fields.Datetime.to_string(last_restic.run_date)
                if last_restic
                else False
            ),
            "restic_snapshots_24h": snapshots_24h,
            "bucket_total_human": (
                _human_bytes(latest_bucket.total_bytes) if latest_bucket else "0 B"
            ),
            "bucket_collected_at": (
                fields.Datetime.to_string(latest_bucket.collected_at)
                if latest_bucket
                else False
            ),
        })
        return base

    @api.model
    def _get_services(self):
        """Active/suspended/expired counts."""
        Service = self.env["hosting.service"]
        return {
            "active": Service.search_count([("state", "=", "active")]),
            "suspended": Service.search_count([("state", "=", "suspended")]),
            "expired": Service.search_count([("state", "=", "expired")]),
        }

    @api.model
    def _get_domains(self):
        """Domain counts for dashboard."""
        Domain = self.env["hosting.domain"]
        today = fields.Date.today()
        return {
            "total": Domain.search_count([("state", "!=", "transferred")]),
            "expiring_30": Domain.search_count([
                ("state", "in", ("active", "expiring_soon")),
                ("date_expiration", "<=", today + timedelta(days=30)),
                ("date_expiration", ">=", today),
            ]),
            "ssl_expiring": Domain.search_count([
                ("ssl_type", "!=", "none"),
                ("ssl_expiry_date", "<=", today + timedelta(days=30)),
                ("ssl_expiry_date", ">=", today),
            ]),
            "no_auto_renew": Domain.search_count([
                ("state", "in", ("active", "expiring_soon")),
                ("auto_renew", "=", False),
            ]),
        }

    @api.model
    def _get_security(self):
        """Audit log and security event counts for dashboard."""
        AuditLog = self.env["hosting.audit.log"]
        SecurityEvent = self.env["hosting.security.event"]
        now = fields.Datetime.now()
        day_ago = now - timedelta(hours=24)
        week_ago = now - timedelta(days=7)

        return {
            "audit_24h": AuditLog.sudo().search_count([
                ("event_date", ">=", day_ago),
            ]),
            "audit_7d": AuditLog.sudo().search_count([
                ("event_date", ">=", week_ago),
            ]),
            "critical_7d": AuditLog.sudo().search_count([
                ("event_date", ">=", week_ago),
                ("severity", "=", "critical"),
            ]),
            "incidents_open": SecurityEvent.search_count([
                ("state", "not in", ("resolved", "false_positive")),
            ]),
        }

    # Domain navigation actions
    @api.model
    def action_view_domains(self):
        return {
            "type": "ir.actions.act_window",
            "name": "Domaines",
            "res_model": "hosting.domain",
            "views": [[False, "list"], [False, "form"]],
            "domain": [("state", "!=", "transferred")],
        }

    @api.model
    def action_view_domains_expiring(self):
        today = fields.Date.today()
        return {
            "type": "ir.actions.act_window",
            "name": "Domaines expirant (30j)",
            "res_model": "hosting.domain",
            "views": [[False, "list"], [False, "form"]],
            "domain": [
                ("state", "in", ("active", "expiring_soon")),
                ("date_expiration", "<=", str(today + timedelta(days=30))),
                ("date_expiration", ">=", str(today)),
            ],
        }

    @api.model
    def action_view_ssl_expiring(self):
        today = fields.Date.today()
        return {
            "type": "ir.actions.act_window",
            "name": "SSL expirant (30j)",
            "res_model": "hosting.domain",
            "views": [[False, "list"], [False, "form"]],
            "domain": [
                ("ssl_type", "!=", "none"),
                ("ssl_expiry_date", "<=", str(today + timedelta(days=30))),
                ("ssl_expiry_date", ">=", str(today)),
            ],
        }

    @api.model
    def action_view_domains_no_auto_renew(self):
        return {
            "type": "ir.actions.act_window",
            "name": "Domaines sans renouvellement auto",
            "res_model": "hosting.domain",
            "views": [[False, "list"], [False, "form"]],
            "domain": [
                ("state", "in", ("active", "expiring_soon")),
                ("auto_renew", "=", False),
            ],
        }

    # Security / Audit navigation actions
    @api.model
    def action_view_audit_log(self):
        return {
            "type": "ir.actions.act_window",
            "name": "Journal d'audit",
            "res_model": "hosting.audit.log",
            "views": [[False, "list"], [False, "form"]],
        }

    @api.model
    def action_view_audit_log_24h(self):
        return {
            "type": "ir.actions.act_window",
            "name": "Audit (24h)",
            "res_model": "hosting.audit.log",
            "views": [[False, "list"], [False, "form"]],
            "context": {"search_default_filter_today": 1},
        }

    @api.model
    def action_view_audit_log_7d(self):
        return {
            "type": "ir.actions.act_window",
            "name": "Audit (7 jours)",
            "res_model": "hosting.audit.log",
            "views": [[False, "list"], [False, "form"]],
            "context": {"search_default_filter_week": 1},
        }

    @api.model
    def action_view_audit_critical_7d(self):
        return {
            "type": "ir.actions.act_window",
            "name": "Critiques (7 jours)",
            "res_model": "hosting.audit.log",
            "views": [[False, "list"], [False, "form"]],
            "context": {
                "search_default_filter_week": 1,
                "search_default_filter_critical": 1,
            },
        }

    @api.model
    def action_view_security_events_open(self):
        return {
            "type": "ir.actions.act_window",
            "name": "Incidents ouverts",
            "res_model": "hosting.security.event",
            "views": [[False, "list"], [False, "form"]],
            "context": {"search_default_filter_active": 1},
        }
