# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import logging

from odoo import api, fields, models, _

from . import hosting_repo_matcher

_logger = logging.getLogger(__name__)


def _human_bytes(n):
    if not n:
        return "0 B"
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if n < 1024.0:
            return f"{n:.2f} {unit}" if unit != "B" else f"{int(n)} {unit}"
        n /= 1024.0
    return f"{n:.2f} EB"


class HostingBackupRepository(models.Model):
    """Dépôt Restic — un record par destination configurée dans le pipeline."""

    _name = "hosting.backup.repository"
    _description = "Dépôt Restic"
    _inherit = ["mail.thread"]
    _order = "category, name"

    name = fields.Char(
        string="Nom du dépôt",
        required=True,
        help="Clé exacte de la destination Restic (ex. 'Internal/Database')",
        tracking=True,
    )
    category = fields.Selection(
        selection=[
            ("internal", "Interne"),
            ("tenant", "Tenant"),
            ("clients", "Clients"),
            ("other", "Autre"),
        ],
        string="Catégorie",
        compute="_compute_category",
        store=True,
    )
    s3_url = fields.Char(
        string="URL S3",
        groups="hosting_management.group_hosting_manager",
    )
    password_file_path = fields.Char(
        string="Fichier mot de passe (info)",
        groups="hosting_management.group_hosting_manager",
    )

    retention_daily = fields.Integer(string="Rétention quotidienne", default=14)
    retention_weekly = fields.Integer(string="Rétention hebdomadaire", default=8)
    retention_monthly = fields.Integer(string="Rétention mensuelle", default=12)

    snapshot_count = fields.Integer(string="Nombre de snapshots", default=0)
    latest_snapshot_date = fields.Datetime(string="Dernier snapshot")
    size_bytes = fields.Float(string="Taille (octets)", default=0.0)
    size_human = fields.Char(string="Taille", compute="_compute_size_human")

    last_watchdog_sync = fields.Datetime(string="Dernière sync watchdog")
    is_stale = fields.Boolean(
        string="Périmé",
        compute="_compute_is_stale",
        store=True,
        help="Vrai si dernier snapshot > 27 h",
    )

    bucket_size_share = fields.Float(
        string="Part du bucket (%)",
        compute="_compute_bucket_size_share",
        digits=(5, 2),
    )

    service_ids = fields.Many2many(
        comodel_name="hosting.service",
        relation="hosting_service_backup_repository_rel",
        column1="repository_id",
        column2="service_id",
        string="Services liés",
    )
    snapshot_ids = fields.One2many(
        comodel_name="hosting.backup.snapshot",
        inverse_name="repository_id",
        string="Snapshots",
    )

    active = fields.Boolean(default=True, tracking=True)
    notes = fields.Text(string="Notes")

    _sql_constraints = [
        ("name_unique", "unique(name)", "Le nom du dépôt doit être unique."),
    ]

    @api.depends("name")
    def _compute_category(self):
        """Auto-categorize based on name prefix.

        Categorization rules are read from system parameters
        ``hosting.repo_category_internal_prefixes`` (comma-separated lowercase
        prefixes) etc., so each tenant can map their own naming convention
        without modifying this module. Falls back to the path heuristic
        below when a name does not match any configured prefix.
        """
        get_param = self.env["ir.config_parameter"].sudo().get_param
        internal = [p.strip().lower() for p in (
            get_param("hosting.repo_category_internal_prefixes") or ""
        ).split(",") if p.strip()]
        tenant = [p.strip().lower() for p in (
            get_param("hosting.repo_category_tenant_prefixes") or ""
        ).split(",") if p.strip()]
        clients = [p.strip().lower() for p in (
            get_param("hosting.repo_category_clients_prefixes") or "clients"
        ).split(",") if p.strip()]
        for repo in self:
            n = (repo.name or "").lower()
            if any(n.startswith(p) for p in internal):
                repo.category = "internal"
            elif any(n.startswith(p) for p in tenant):
                repo.category = "tenant"
            elif any(n.startswith(p) for p in clients):
                repo.category = "clients"
            else:
                repo.category = "other"

    @api.depends("size_bytes")
    def _compute_size_human(self):
        for repo in self:
            repo.size_human = _human_bytes(repo.size_bytes)

    @api.depends("latest_snapshot_date")
    def _compute_is_stale(self):
        threshold = fields.Datetime.subtract(fields.Datetime.now(), hours=27)
        for repo in self:
            if not repo.latest_snapshot_date:
                repo.is_stale = False
            else:
                repo.is_stale = repo.latest_snapshot_date < threshold

    def _compute_bucket_size_share(self):
        Bucket = self.env["hosting.backup.bucket.snapshot"]
        latest_bucket = Bucket.search([], order="collected_at desc", limit=1)
        total = latest_bucket.total_bytes if latest_bucket else 0.0
        for repo in self:
            if total > 0 and repo.size_bytes > 0:
                repo.bucket_size_share = (repo.size_bytes / total) * 100.0
            else:
                repo.bucket_size_share = 0.0

    def name_get(self):
        return [(r.id, r.name) for r in self]

    # ── Auto-match repositories ↔ hosting services ────────────────────────

    def _find_matching_services(self):
        """Return services whose (software, client) pair matches this repo."""
        self.ensure_one()
        cfg = hosting_repo_matcher.get_config(self.env)
        repo_pair = hosting_repo_matcher.parse_repo(
            self.name or "",
            cfg["client_synonyms"],
            cfg["sw_synonyms"],
            cfg["bucket_prefixes"],
            cfg["clients_prefix"],
        )
        if repo_pair == (None, None):
            return self.env["hosting.service"]
        candidates = self.env["hosting.service"].search(
            [("active", "=", True), ("state", "=", "active")]
        )
        matched_ids = []
        for svc in candidates:
            svc_pair = hosting_repo_matcher.parse_service(
                svc.name or "",
                cfg["client_synonyms"],
                cfg["sw_synonyms"],
            )
            if svc_pair == repo_pair and all(svc_pair):
                matched_ids.append(svc.id)
        return self.env["hosting.service"].browse(matched_ids)

    def auto_link_services(self):
        """Link this repo to every service whose name-pair matches it.

        Returns the number of links newly added (existing links preserved).
        """
        added = 0
        for repo in self:
            services = repo._find_matching_services()
            new_services = services - repo.service_ids
            if new_services:
                repo.service_ids = [(4, sid) for sid in new_services.ids]
                added += len(new_services)
                _logger.info(
                    "Auto-linked repo %s to %d service(s): %s",
                    repo.name,
                    len(new_services),
                    new_services.mapped("name"),
                )
        return added

    def action_auto_link_services(self):
        added = self.auto_link_services()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Liaison automatique"),
                "message": (
                    _("%d nouveau(x) service(s) lié(s) à ce dépôt.") % added
                    if added
                    else _("Aucun nouveau service à lier — déjà à jour.")
                ),
                "type": "success" if added else "info",
                "sticky": False,
            },
        }

    @api.model_create_multi
    def create(self, vals_list):
        repos = super().create(vals_list)
        # Try to auto-link any matching services. Best-effort: a failure here
        # should never block repo creation (webhook ingestion must succeed).
        for repo in repos:
            try:
                repo.auto_link_services()
            except Exception:  # noqa: BLE001
                _logger.exception(
                    "Auto-link failed for new repo %s — continuing", repo.name
                )
        return repos

    @api.model
    def _cron_auto_link_services(self):
        """Re-run auto-match across all active repos. Idempotent."""
        repos = self.search([("active", "=", True)])
        total = 0
        for repo in repos:
            try:
                total += repo.auto_link_services()
            except Exception:  # noqa: BLE001
                _logger.exception("Auto-link failed for repo %s", repo.name)
        _logger.info(
            "Restic auto-link cron: %d new links across %d repos",
            total,
            len(repos),
        )
        return total
