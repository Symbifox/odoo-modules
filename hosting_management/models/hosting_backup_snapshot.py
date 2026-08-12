# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import api, fields, models


def _human_bytes(n):
    if not n:
        return "0 B"
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if n < 1024.0:
            return f"{n:.2f} {unit}" if unit != "B" else f"{int(n)} {unit}"
        n /= 1024.0
    return f"{n:.2f} EB"


class HostingBackupSnapshot(models.Model):
    """Snapshot Restic individuel (1 par container, 1+ par hosting.backup.line)."""

    _name = "hosting.backup.snapshot"
    _description = "Snapshot Restic"
    _order = "snapshot_date desc, id desc"

    repository_id = fields.Many2one(
        comodel_name="hosting.backup.repository",
        string="Dépôt",
        ondelete="cascade",
        index=True,
    )
    snapshot_id = fields.Char(string="ID Restic", required=True, index=True)
    snapshot_date = fields.Datetime(string="Date snapshot", required=True)
    container = fields.Char(string="Conteneur Docker")
    destination = fields.Char(
        string="Destination",
        help="Chemin de destination dans le payload Restic (= name du repo si liaison ok)",
    )
    files_new = fields.Integer(string="Fichiers ajoutés", default=0)
    data_added_bytes = fields.Float(string="Données ajoutées (octets)", default=0.0)
    data_added_human = fields.Char(
        string="Données ajoutées",
        compute="_compute_data_added_human",
    )
    duration_sec = fields.Float(string="Durée (s)", default=0.0)
    run_line_id = fields.Many2one(
        comodel_name="hosting.backup.line",
        string="Ligne d'exécution",
        ondelete="set null",
        index=True,
    )
    run_id = fields.Many2one(
        related="run_line_id.run_id",
        string="Exécution",
        store=True,
    )

    @api.depends("data_added_bytes")
    def _compute_data_added_human(self):
        for s in self:
            s.data_added_human = _human_bytes(s.data_added_bytes)
