# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import logging
from datetime import timedelta

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class HostingHealthCheck(models.Model):
    _name = "hosting.health.check"
    _description = "Vérification de santé du service"
    _order = "check_date desc"

    service_id = fields.Many2one(
        comodel_name="hosting.service",
        string="Service",
        required=True,
        ondelete="cascade",
        index=True,
    )
    check_date = fields.Datetime(
        string="Date de vérification",
        default=fields.Datetime.now,
        required=True,
        index=True,
    )
    status = fields.Selection(
        selection=[
            ("up", "En ligne"),
            ("degraded", "Dégradé"),
            ("down", "Hors ligne"),
            ("timeout", "Délai dépassé"),
        ],
        string="État",
        required=True,
    )
    response_time_ms = fields.Integer(
        string="Temps de réponse (ms)",
        help="Temps de réponse en millisecondes",
    )
    http_status_code = fields.Integer(
        string="Code de statut HTTP",
    )
    error_message = fields.Text(
        string="Message d'erreur",
    )
    excluded_from_stats = fields.Boolean(
        string="Exclu des statistiques",
        default=False,
        index=True,
        help="Exclure cette vérification du calcul de disponibilité et du tableau "
        "de bord (p. ex. panne due à un enjeu externe : fournisseur réseau, DNS "
        "client, maintenance planifiée par un tiers). L'enregistrement reste "
        "visible dans l'historique.",
    )
    exclusion_reason = fields.Text(
        string="Raison de l'exclusion",
        help="Brève note expliquant pourquoi cette vérification est exclue "
        "(cause externe, nom du fournisseur, numéro d'incident, etc.).",
    )

    # Champs liés pour les rapports
    partner_id = fields.Many2one(
        related="service_id.partner_id",
        store=True,
        index=True,
    )
    software_id = fields.Many2one(
        related="service_id.software_id",
        store=True,
    )

    def action_toggle_exclusion(self):
        """Bascule l'exclusion des statistiques pour les vérifications sélectionnées."""
        for record in self:
            record.excluded_from_stats = not record.excluded_from_stats
            if not record.excluded_from_stats:
                record.exclusion_reason = False
        # Invalidate the cached uptime_30d compute so the service records refresh.
        self.mapped("service_id").invalidate_recordset(["uptime_30d"])
        return True

    # -- Consolidation + retention cron --

    @api.model
    def _cron_consolidate_and_purge(self):
        """Roll raw checks older than the retention window into daily snapshots,
        then delete them. Snapshots remain available for long-term reporting via
        ``hosting.health.daily.snapshot``.

        Retention controlled by ``hosting.health_check_retention_days`` (default 30).
        Only consolidates days that are fully past — never the running day.
        """
        ICP = self.env["ir.config_parameter"].sudo()
        retention_days = int(ICP.get_param("hosting.health_check_retention_days", "30"))
        if retention_days <= 0:
            return

        cutoff = fields.Datetime.now() - timedelta(days=retention_days)
        # Aggregate raw rows older than cutoff into one row per (service, day)
        self.env.cr.execute(
            """
            INSERT INTO hosting_health_daily_snapshot
                (service_id, partner_id, software_id, snapshot_date,
                 check_count, up_count, uptime_pct, avg_response_ms,
                 excluded_count, create_uid, create_date, write_uid, write_date)
            SELECT
                hc.service_id,
                s.partner_id,
                s.software_id,
                date_trunc('day', hc.check_date)::date AS snapshot_date,
                COUNT(*) AS check_count,
                COUNT(*) FILTER (
                    WHERE hc.status = 'up'
                      AND COALESCE(hc.excluded_from_stats, false) = false
                ) AS up_count,
                COALESCE(
                    100.0 * COUNT(*) FILTER (
                        WHERE hc.status = 'up'
                          AND COALESCE(hc.excluded_from_stats, false) = false
                    )::numeric
                    / NULLIF(COUNT(*) FILTER (
                        WHERE COALESCE(hc.excluded_from_stats, false) = false
                    ), 0),
                    100.0
                ) AS uptime_pct,
                COALESCE(AVG(hc.response_time_ms) FILTER (
                    WHERE hc.status = 'up'
                ), 0)::int AS avg_response_ms,
                COUNT(*) FILTER (
                    WHERE COALESCE(hc.excluded_from_stats, false) = true
                ) AS excluded_count,
                1, NOW(), 1, NOW()
            FROM hosting_health_check hc
            JOIN hosting_service s ON s.id = hc.service_id
            WHERE hc.check_date < %s
            GROUP BY hc.service_id, s.partner_id, s.software_id,
                     date_trunc('day', hc.check_date)::date
            ON CONFLICT (service_id, snapshot_date) DO UPDATE
                SET check_count    = EXCLUDED.check_count,
                    up_count       = EXCLUDED.up_count,
                    uptime_pct     = EXCLUDED.uptime_pct,
                    avg_response_ms= EXCLUDED.avg_response_ms,
                    excluded_count = EXCLUDED.excluded_count,
                    write_date     = NOW()
            """,
            (cutoff,),
        )
        snap_rows = self.env.cr.rowcount

        self.env.cr.execute(
            "DELETE FROM hosting_health_check WHERE check_date < %s",
            (cutoff,),
        )
        deleted = self.env.cr.rowcount
        if deleted:
            _logger.info(
                "hosting_health_check: consolidated %d daily snapshots and "
                "purged %d raw rows older than %d days (cutoff=%s)",
                snap_rows, deleted, retention_days, cutoff.isoformat(),
            )
