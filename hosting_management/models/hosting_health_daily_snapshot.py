# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).

from odoo import api, fields, models


class HostingHealthDailySnapshot(models.Model):
    _name = "hosting.health.daily.snapshot"
    _description = "Snapshot quotidien de disponibilité"
    _order = "snapshot_date desc, service_id"
    _sql_constraints = [
        (
            "service_date_unique",
            "unique(service_id, snapshot_date)",
            "Un seul snapshot par service et par jour.",
        ),
    ]

    service_id = fields.Many2one(
        comodel_name="hosting.service",
        string="Service",
        required=True,
        ondelete="cascade",
        index=True,
    )
    snapshot_date = fields.Date(
        string="Date",
        required=True,
        index=True,
    )
    check_count = fields.Integer(
        string="Vérifications agrégées",
        required=True,
    )
    up_count = fields.Integer(
        string="Vérifications « up »",
        required=True,
    )
    uptime_pct = fields.Float(
        string="Disponibilité (%)",
        digits=(5, 2),
        required=True,
    )
    avg_response_ms = fields.Integer(
        string="Temps de réponse moyen (ms)",
    )
    excluded_count = fields.Integer(
        string="Vérifications exclues",
        help="Vérifications marquées excluded_from_stats au moment de la "
        "consolidation (ne sont pas comptées dans le pourcentage).",
    )
    archive_filename = fields.Char(
        string="Archive 7z",
        help="Nom du fichier 7z sur Nextcloud contenant les données brutes "
        "consolidées par ce snapshot.",
    )

    partner_id = fields.Many2one(
        related="service_id.partner_id",
        store=True,
        index=True,
    )
    software_id = fields.Many2one(
        related="service_id.software_id",
        store=True,
    )

    @api.depends("uptime_pct", "snapshot_date", "service_id")
    def _compute_display_name(self):
        for rec in self:
            if rec.service_id and rec.snapshot_date:
                rec.display_name = (
                    f"{rec.service_id.name} – {rec.snapshot_date} "
                    f"({rec.uptime_pct:.1f}%)"
                )
            else:
                rec.display_name = "Snapshot"
