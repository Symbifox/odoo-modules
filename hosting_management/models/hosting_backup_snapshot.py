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
    _description = "Restic snapshot"
    _order = "snapshot_date desc, id desc"

    repository_id = fields.Many2one(
        comodel_name="hosting.backup.repository",
        string="Repository",
        ondelete="cascade",
        index=True,
    )
    snapshot_id = fields.Char(string="Restic ID", required=True, index=True)
    snapshot_date = fields.Datetime(string="Snapshot date", required=True)
    container = fields.Char(string="Docker container")
    destination = fields.Char(
        string="Destination",
        help="Destination path in the Restic payload (= repo name when linked)",
    )
    files_new = fields.Integer(string="Files added", default=0)
    data_added_bytes = fields.Float(string="Data added (bytes)", default=0.0)
    data_added_human = fields.Char(
        string="Data added",
        compute="_compute_data_added_human",
    )
    duration_sec = fields.Float(string="Duration (s)", default=0.0)
    run_line_id = fields.Many2one(
        comodel_name="hosting.backup.line",
        string="Run line",
        ondelete="set null",
        index=True,
    )
    run_id = fields.Many2one(
        related="run_line_id.run_id",
        string="Run",
        store=True,
    )

    @api.depends("data_added_bytes")
    def _compute_data_added_human(self):
        for s in self:
            s.data_added_human = _human_bytes(s.data_added_bytes)
