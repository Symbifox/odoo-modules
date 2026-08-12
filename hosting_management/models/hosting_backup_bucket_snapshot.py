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


class HostingBackupBucketSnapshot(models.Model):
    """Mesure ponctuelle de la taille du bucket S3 (un point par run watchdog)."""

    _name = "hosting.backup.bucket.snapshot"
    _description = "Mesure du bucket Restic"
    _order = "collected_at desc, id desc"

    collected_at = fields.Datetime(string="Date de collecte", required=True, index=True)
    host = fields.Char(string="Hôte source")
    total_objects = fields.Integer(string="Objets")
    total_bytes = fields.Float(string="Taille (octets)")
    total_human = fields.Char(string="Taille", compute="_compute_total_human")

    @api.depends("total_bytes")
    def _compute_total_human(self):
        for r in self:
            r.total_human = _human_bytes(r.total_bytes)

    @api.model
    def prune_old(self, days=180):
        """Cron : supprime les mesures plus vieilles que N jours."""
        cutoff = fields.Datetime.subtract(fields.Datetime.now(), days=days)
        old = self.search([("collected_at", "<", cutoff)])
        n = len(old)
        old.unlink()
        return n
