# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import logging

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
    service_id = fields.Many2one(
        comodel_name="hosting.service",
        string="Service",
        index=True,
        ondelete="set null",
        help="Hosting service this snapshot belongs to, resolved from the "
             "Docker container name observed in the Restic payload. Left "
             "empty when the container name matches no service, or more "
             "than one: a wrong link reads as a backup that does not exist.",
    )
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

    # ── Service resolution by observed container ──────────────────────────

    @api.model
    def _resolve_service_ids(self, containers):
        """Map container names to a single hosting.service id each.

        The container name is what the backup script actually saw on the
        host, so it is a stronger signal than a naming convention. A name
        that matches several services (one container serving many tenants,
        such as a shared CubeBackup) resolves to nothing: the service form
        keeps its manual repository link instead of claiming a snapshot
        that may belong to a neighbour.
        """
        names = sorted({c for c in containers if c})
        if not names:
            return {}
        Service = self.env["hosting.service"]
        # Une fiche brouillon créée pour préparer un remplacement portait le même
        # conteneur et gelait la résolution du service vivant : seuls les états
        # vivants comptent comme concurrents.
        services = Service.sudo().search_read(
            [
                ("docker_container", "in", names),
                ("active", "=", True),
                ("state", "in", list(Service._BACKUP_LIVE_STATES)),
            ],
            ["docker_container"],
        )
        by_name = {}
        for svc in services:
            by_name.setdefault(svc["docker_container"], []).append(svc["id"])
        return {name: ids[0] for name, ids in by_name.items() if len(ids) == 1}

    @api.model_create_multi
    def create(self, vals_list):
        pending = [
            v.get("container")
            for v in vals_list
            if v.get("container") and not v.get("service_id")
        ]
        resolved = self._resolve_service_ids(pending) if pending else {}
        if resolved:
            # Copie : un appelant qui réutilise son dict d'un tour à l'autre
            # verrait le service du premier tour recopié sur tous les suivants,
            # donc un lien faux, donc une sauvegarde qui n'existe pas.
            vals_list = [
                dict(vals, service_id=resolved[vals["container"]])
                if not vals.get("service_id")
                and vals.get("container") in resolved
                else vals
                for vals in vals_list
            ]
        return super().create(vals_list)

    @api.model
    def _backfill_service_ids(self, limit=None):
        """Resolve service_id on snapshots ingested before the field existed."""
        domain = [("service_id", "=", False), ("container", "!=", False)]
        snaps = self.sudo().search_read(domain, ["container"], limit=limit)
        resolved = self._resolve_service_ids([s["container"] for s in snaps])
        by_service = {}
        for snap in snaps:
            service_id = resolved.get(snap["container"])
            if service_id:
                by_service.setdefault(service_id, []).append(snap["id"])
        touched = 0
        for service_id, ids in by_service.items():
            self.sudo().browse(ids).write({"service_id": service_id})
            touched += len(ids)
        _logger.info(
            "Backup snapshots: %d snapshot(s) linked across %d container(s)",
            touched,
            len(resolved),
        )
        return touched
