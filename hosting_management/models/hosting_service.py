# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import ipaddress
import logging
import time
from datetime import timedelta
from urllib.parse import urlparse

from markupsafe import escape as _esc

from odoo import api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)

try:
    import pytz
except ImportError:
    pytz = None


class HostingService(models.Model):
    _name = "hosting.service"
    _description = "Service d'hébergement"
    _order = "code"
    _inherit = ["mail.thread", "mail.activity.mixin"]

    name = fields.Char(
        string="Nom du service",
        required=True,
        tracking=True,
    )
    code = fields.Char(
        string="Référence",
        readonly=True,
        copy=False,
        default="New",
    )
    partner_id = fields.Many2one(
        comodel_name="res.partner",
        string="Client",
        required=True,
        tracking=True,
        domain="[('is_company', '=', True)]",
    )
    user_id = fields.Many2one(
        comodel_name="res.users",
        string="Responsable",
        default=lambda self: self.env.user,
        tracking=True,
    )
    software_id = fields.Many2one(
        comodel_name="hosting.software",
        string="Logiciel",
        required=True,
        tracking=True,
    )
    installed_version_id = fields.Many2one(
        comodel_name="hosting.software.version",
        string="Version installée",
        domain="[('software_id', '=', software_id)]",
        tracking=True,
    )
    target_version_id = fields.Many2one(
        comodel_name="hosting.software.version",
        string="Version cible",
        domain="[('software_id', '=', software_id)]",
        help="Version vers laquelle mettre à jour",
    )
    version_policy = fields.Selection(
        selection=[
            ("latest", "Toujours la dernière"),
            ("lts", "LTS uniquement"),
            ("manual", "Mises à jour manuelles"),
            ("frozen", "Gelée (aucune mise à jour)"),
        ],
        string="Politique de version",
        default="manual",
        required=True,
        tracking=True,
    )
    version_notes = fields.Text(
        string="Notes de version",
        help="Raison de la version gelée ou instructions spéciales",
    )
    update_available = fields.Boolean(
        string="Mise à jour disponible",
        compute="_compute_update_available",
        store=True,
    )

    # Environnement
    environment = fields.Selection(
        selection=[
            ("production", "Production"),
            ("staging", "Préproduction"),
            ("development", "Développement"),
            ("testing", "Test"),
        ],
        string="Environnement",
        default="production",
        required=True,
        tracking=True,
    )
    linked_production_id = fields.Many2one(
        comodel_name="hosting.service",
        string="Production liée",
        domain="[('environment', '=', 'production'), ('software_id', '=', software_id), ('partner_id', '=', partner_id)]",
        help="Lier la préproduction/développement au service de production",
    )

    # Connexion
    server_url = fields.Char(
        string="URL du service",
        tracking=True,
    )
    accepted_http_code_ids = fields.Many2many(
        comodel_name="hosting.accepted.http.code",
        string="Codes HTTP acceptés",
        help="Codes HTTP (ex. 404) considérés comme 'en ligne' pour ce service.",
    )
    domain_id = fields.Many2one(
        comodel_name="hosting.domain",
        string="Domaine",
        tracking=True,
    )
    domain_name = fields.Char(
        string="Nom de domaine",
    )

    # Serveur / Docker
    server_id = fields.Many2one(
        comodel_name="hosting.server",
        string="Serveur",
        tracking=True,
        help="Serveur sur lequel ce service est hébergé",
    )
    docker_host = fields.Char(
        string="Hôte Docker",
        compute="_compute_docker_host",
        store=True,
        help="Nom d'hôte du serveur (calculé depuis le serveur)",
    )
    docker_container = fields.Char(
        string="Nom du conteneur",
        help="Nom du conteneur Docker (ex. : vaultwarden-myorg)",
    )

    # Stockage
    storage_quota_gb = fields.Float(
        string="Quota de stockage (Go)",
        digits=(10, 2),
    )
    storage_used_gb = fields.Float(
        string="Stockage utilisé (Go)",
        digits=(10, 2),
    )
    storage_used_percent = fields.Float(
        string="Stockage utilisé (%)",
        compute="_compute_storage_percent",
        store=True,
    )
    storage_alert = fields.Boolean(
        string="Alerte de stockage",
        compute="_compute_storage_alert",
        store=True,
        help="Vrai si l'utilisation du stockage dépasse le seuil",
    )
    storage_alert_threshold = fields.Float(
        string="Seuil d'alerte (%)",
        default=80.0,
    )

    # Configuration de mesure de stockage
    storage_check_type = fields.Selection(
        selection=[
            ("none", "Aucun"),
            ("nextcloud_db", "Base Nextcloud (filecache)"),
            ("postgres", "Base PostgreSQL"),
            ("mariadb", "Base MariaDB"),
            ("container_directory", "Répertoire conteneur"),
            ("docker_volume", "Volume Docker"),
            ("host_directory", "Répertoire hôte"),
        ],
        string="Type de mesure",
        default="none",
    )
    storage_check_path = fields.Char(
        string="Chemin de mesure",
        help="Chemin du répertoire à mesurer (conteneur, volume ou hôte)",
    )
    storage_check_db_name = fields.Char(
        string="Nom de la base",
        help="Nom de la base de données pour la mesure PostgreSQL/MariaDB/Nextcloud",
    )
    storage_check_db_user = fields.Char(
        string="Utilisateur BD",
        help="Utilisateur de la base de données pour la mesure",
    )
    storage_last_check = fields.Datetime(
        string="Dernière mesure",
        readonly=True,
    )

    # Dates
    date_start = fields.Date(
        string="Date de début",
        default=fields.Date.today,
        tracking=True,
    )
    date_expiration = fields.Date(
        string="Date d'expiration",
        tracking=True,
    )
    days_until_expiration = fields.Integer(
        string="Jours avant expiration",
        compute="_compute_days_until_expiration",
    )
    expiration_activity_created = fields.Boolean(
        string="Activité d'expiration créée",
        default=False,
        copy=False,
        help="Indicateur pour prévenir la création d'activités d'expiration en double",
    )
    disconnect_acknowledged_date = fields.Date(
        string="Date d'accusé de non-renouvellement",
        readonly=True,
        copy=False,
        tracking=True,
        help="Date à laquelle la non-reconduction du service a été confirmée avec le client.",
    )
    disconnect_reason = fields.Char(
        string="Motif de non-renouvellement",
        copy=False,
        tracking=True,
        help="Contexte court : migration vers X, décision client, fin de contrat, etc.",
    )

    # État
    state = fields.Selection(
        selection=[
            ("draft", "Brouillon"),
            ("active", "Actif"),
            ("to_disconnect", "À déconnecter"),
            ("expired", "Expiré"),
            ("suspended", "Suspendu"),
            ("cancelled", "Annulé"),
        ],
        string="État",
        default="draft",
        required=True,
        tracking=True,
    )

    # Facturation
    contract_id = fields.Many2one(
        comodel_name="contract.contract",
        string="Contrat",
        tracking=True,
        help="Lien vers le contrat pour la facturation",
    )

    # Étiquettes
    tag_ids = fields.Many2many(
        comodel_name="hosting.service.tag",
        relation="hosting_service_tag_rel",
        column1="service_id",
        column2="tag_id",
        string="Étiquettes",
    )

    # Historique de mise à jour
    update_log_ids = fields.One2many(
        comodel_name="hosting.update.log",
        inverse_name="service_id",
        string="Historique de mise à jour",
    )

    # Vérification de santé
    health_check_ids = fields.One2many(
        comodel_name="hosting.health.check",
        inverse_name="service_id",
        string="Vérifications de santé",
    )
    health_daily_snapshot_ids = fields.One2many(
        comodel_name="hosting.health.daily.snapshot",
        inverse_name="service_id",
        string="Snapshots quotidiens (>30j)",
    )

    # Planifications de maintenance
    maintenance_schedule_ids = fields.One2many(
        comodel_name="hosting.maintenance.schedule",
        inverse_name="service_id",
        string="Planifications de maintenance",
    )
    maintenance_schedule_count = fields.Integer(
        string="Tâches de maintenance",
        compute="_compute_maintenance_schedule_count",
    )
    maintenance_overdue_count = fields.Integer(
        string="Maintenance en retard",
        compute="_compute_maintenance_schedule_count",
    )
    last_health_status = fields.Selection(
        selection=[
            ("up", "En ligne"),
            ("degraded", "Dégradé"),
            ("down", "Hors ligne"),
            ("timeout", "Délai dépassé"),
        ],
        string="État de santé",
        compute="_compute_health_status",
        store=True,
    )
    last_health_check = fields.Datetime(
        string="Dernière vérification de santé",
        compute="_compute_health_status",
        store=True,
    )
    is_service_up = fields.Boolean(
        string="Service en ligne",
        compute="_compute_health_status",
        store=True,
    )
    uptime_30d = fields.Float(
        string="Disponibilité 30j (%)",
        compute="_compute_uptime_30d",
        digits=(5, 2),
    )
    health_check_count = fields.Integer(
        string="Nombre de vérifications de santé",
        compute="_compute_health_check_count",
    )
    health_alert_active = fields.Boolean(
        string="Alerte de santé active",
        default=False,
        help="Indique qu'une alerte de panne a été envoyée et que "
        "le service n'est pas encore rétabli.",
    )

    active = fields.Boolean(
        string="Actif",
        default=True,
    )

    # ── Sauvegardes Restic ────────────────────────────────────────────────
    restic_repository_ids = fields.Many2many(
        comodel_name="hosting.backup.repository",
        relation="hosting_service_backup_repository_rel",
        column1="service_id",
        column2="repository_id",
        string="Dépôts Restic",
        help="Dépôts Restic qui contiennent les sauvegardes de ce service",
    )
    restic_snapshot_count = fields.Integer(
        string="Snapshots Restic",
        compute="_compute_restic_summary",
    )
    restic_last_backup = fields.Datetime(
        string="Dernière sauvegarde Restic",
        compute="_compute_restic_summary",
    )
    restic_is_stale = fields.Boolean(
        string="Sauvegarde Restic périmée",
        compute="_compute_restic_summary",
        help="Vrai si au moins un dépôt lié est périmé (>27 h)",
    )

    @api.depends(
        "restic_repository_ids.snapshot_count",
        "restic_repository_ids.latest_snapshot_date",
        "restic_repository_ids.is_stale",
    )
    def _compute_restic_summary(self):
        for s in self:
            repos = s.restic_repository_ids
            s.restic_snapshot_count = sum(repos.mapped("snapshot_count"))
            dates = [d for d in repos.mapped("latest_snapshot_date") if d]
            s.restic_last_backup = max(dates) if dates else False
            s.restic_is_stale = any(repos.mapped("is_stale"))

    _sql_constraints = [
        ("code_uniq", "UNIQUE(code)", "La référence du service doit être unique !"),
    ]

    @api.constrains("server_url")
    def _check_server_url(self):
        """Reject URLs pointing to private/internal networks (SSRF prevention)."""
        for record in self:
            url = record.server_url
            if not url:
                continue
            parsed = urlparse(url)
            if parsed.scheme not in ("http", "https"):
                raise ValidationError(
                    f"L'URL du service doit utiliser http:// ou https:// (reçu : {parsed.scheme}://)"
                )
            hostname = parsed.hostname or ""
            if hostname in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
                raise ValidationError(
                    "L'URL du service ne peut pas pointer vers localhost."
                )
            try:
                addr = ipaddress.ip_address(hostname)
                if addr.is_private or addr.is_loopback or addr.is_link_local:
                    raise ValidationError(
                        f"L'URL du service ne peut pas pointer vers une adresse privée ({hostname})."
                    )
            except ValueError:
                pass  # hostname is not an IP literal — that's normal (domain name)
            if hostname.endswith(".internal") or hostname.endswith(".local"):
                raise ValidationError(
                    f"L'URL du service ne peut pas pointer vers un domaine interne ({hostname})."
                )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("code", "New") == "New":
                vals["code"] = self.env["ir.sequence"].next_by_code(
                    "hosting.service"
                ) or "New"
        records = super().create(vals_list)
        AuditLog = self.env["hosting.audit.log"]
        for rec in records:
            AuditLog._log_event(
                action_type="create",
                category="ops",
                description=f"Service créé : {rec.name} ({rec.code})",
                res_model=self._name,
                res_id=rec.id,
                res_name=rec.display_name,
                service_id=rec.id,
                server_id=rec.server_id.id if rec.server_id else None,
            )
        return records

    def write(self, vals):
        # Capture state changes before write
        if "state" in vals:
            state_labels = dict(self._fields["state"].selection)
            for rec in self:
                old_state = rec.state
                new_state = vals["state"]
                if old_state != new_state:
                    severity = (
                        "warning"
                        if new_state in ("suspended", "expired", "cancelled")
                        else "info"
                    )
                    self.env["hosting.audit.log"]._log_event(
                        action_type="state_change",
                        category="ops",
                        description=(
                            f"État du service {rec.name} : "
                            f"{state_labels.get(old_state, old_state)} → "
                            f"{state_labels.get(new_state, new_state)}"
                        ),
                        res_model=self._name,
                        res_id=rec.id,
                        res_name=rec.display_name,
                        service_id=rec.id,
                        server_id=rec.server_id.id if rec.server_id else None,
                        field_name="state",
                        old_value=old_state,
                        new_value=new_state,
                        severity=severity,
                    )
        return super().write(vals)

    def unlink(self):
        AuditLog = self.env["hosting.audit.log"]
        for rec in self:
            AuditLog._log_event(
                action_type="unlink",
                category="ops",
                description=f"Service supprimé : {rec.name} ({rec.code})",
                res_model=self._name,
                res_id=rec.id,
                res_name=rec.display_name,
                service_id=rec.id,
                server_id=rec.server_id.id if rec.server_id else None,
                severity="warning",
            )
        return super().unlink()

    @api.depends(
        "installed_version_id",
        "installed_version_id.version",
        "software_id.latest_version",
        "software_id.version_ids",
        "software_id.version_ids.is_lts",
        "software_id.version_ids.version",
        "version_policy",
    )
    def _compute_update_available(self):
        """A service is "update_available" if its installed version is older
        than the policy-appropriate target.

        - frozen: never flag
        - lts: target = highest `is_lts=True` version of the software
        - latest/manual: target = software.latest_version (the upstream stable)
        """
        for record in self:
            if record.version_policy == "frozen":
                record.update_available = False
                continue
            if not record.installed_version_id:
                record.update_available = False
                continue

            target = record._compute_update_target()
            if not target:
                record.update_available = False
                continue
            installed = record.installed_version_id.version
            record.update_available = self._is_version_older(installed, target)

    def _compute_update_target(self):
        """Return the version string the service should compare against,
        respecting `version_policy`. Returns None if no target exists.
        """
        self.ensure_one()
        if self.version_policy == "lts":
            lts_versions = self.software_id.version_ids.filtered(
                lambda v: v.is_lts and v.version
            )
            if not lts_versions:
                return None
            # Highest parsed semver among LTS versions
            ranked = []
            for v in lts_versions:
                parsed = self._parse_version(v.version)
                if parsed:
                    ranked.append((parsed, v.version))
            if not ranked:
                return None
            ranked.sort(reverse=True)
            return ranked[0][1]
        # latest / manual / (anything else): track upstream stable
        return self.software_id.latest_version or None

    @staticmethod
    def _parse_version(version_str):
        """Analyser une chaîne de version en composants comparables.

        Gère les formats comme :
        - "25.04.8.1" -> (25, 4, 8, 1)
        - "7.4.2" -> (7, 4, 2)
        - "18.0-20260131" -> (18, 0, 20260131)
        - "v1.2.3" -> (1, 2, 3)

        Retourne un tuple d'entiers pour la comparaison, ou None si non analysable.
        """
        if not version_str:
            return None

        # Retirer les préfixes communs
        version_str = version_str.lstrip("v").lstrip("V")

        # Séparer par points ou tirets
        import re
        parts = re.split(r"[.\-]", version_str)

        try:
            return tuple(int(p) for p in parts if p.isdigit() or p.lstrip("-").isdigit())
        except (ValueError, TypeError):
            return None

    @classmethod
    def _is_version_older(cls, installed, latest):
        """Vérifier si la version installée est plus ancienne que la dernière version.

        Retourne True si installée < dernière (mise à jour disponible).
        Retourne False si installée >= dernière (aucune mise à jour nécessaire).
        """
        if installed == latest:
            return False

        installed_parsed = cls._parse_version(installed)
        latest_parsed = cls._parse_version(latest)

        if installed_parsed and latest_parsed:
            max_len = max(len(installed_parsed), len(latest_parsed))
            installed_padded = installed_parsed + (0,) * (max_len - len(installed_parsed))
            latest_padded = latest_parsed + (0,) * (max_len - len(latest_parsed))
            return installed_padded < latest_padded

        # If either version can't be parsed, we can't determine update status
        # Don't flag as needing update based on string inequality alone
        return False

    @api.depends("storage_quota_gb", "storage_used_gb")
    def _compute_storage_percent(self):
        for record in self:
            if record.storage_quota_gb > 0:
                record.storage_used_percent = (
                    record.storage_used_gb / record.storage_quota_gb
                ) * 100
            else:
                record.storage_used_percent = 0.0

    @api.depends("storage_used_percent", "storage_alert_threshold", "storage_quota_gb")
    def _compute_storage_alert(self):
        for record in self:
            threshold = record.storage_alert_threshold or 80.0
            if record.storage_quota_gb and record.storage_quota_gb > 0:
                record.storage_alert = record.storage_used_percent >= threshold
            else:
                record.storage_alert = False

    @api.depends("date_expiration")
    def _compute_days_until_expiration(self):
        today = fields.Date.today()
        for record in self:
            if record.date_expiration:
                delta = record.date_expiration - today
                record.days_until_expiration = delta.days
            else:
                record.days_until_expiration = 0

    @api.depends("server_id", "server_id.hostname")
    def _compute_docker_host(self):
        for record in self:
            record.docker_host = record.server_id.hostname if record.server_id else False

    @api.depends("health_check_ids", "health_check_ids.status", "health_check_ids.check_date")
    def _compute_health_status(self):
        # LATERAL JOIN: one indexed lookup per service via
        # (service_id, check_date DESC). DISTINCT ON would still scan every
        # row in each service's history because Postgres has no skip-scan.
        latest_by_service = {}
        if self.ids:
            self.env.cr.execute(
                """
                SELECT s.id, hc.status, hc.check_date
                FROM (SELECT unnest(%s::integer[]) AS id) s
                LEFT JOIN LATERAL (
                    SELECT status, check_date
                    FROM hosting_health_check
                    WHERE service_id = s.id
                    ORDER BY check_date DESC
                    LIMIT 1
                ) hc ON TRUE
                """,
                (list(self.ids),),
            )
            latest_by_service = {
                row[0]: (row[1], row[2]) for row in self.env.cr.fetchall()
            }
        for record in self:
            status, check_date = latest_by_service.get(record.id, (None, None))
            if status:
                record.last_health_status = status
                record.last_health_check = check_date
                record.is_service_up = status == "up"
            else:
                record.last_health_status = False
                record.last_health_check = False
                record.is_service_up = True

    def _compute_uptime_30d(self):
        """Calculer le pourcentage de disponibilité des 30 derniers jours.

        Exclut les vérifications marquées `excluded_from_stats` (pannes
        tributaires de causes externes) pour éviter de biaiser la moyenne.
        """
        stats = {}
        if self.ids:
            thirty_days_ago = fields.Datetime.now() - timedelta(days=30)
            self.env.cr.execute(
                """
                SELECT service_id,
                       COUNT(*) FILTER (WHERE status = 'up') AS up_count,
                       COUNT(*) AS total_count
                FROM hosting_health_check
                WHERE service_id IN %s
                  AND check_date >= %s
                  AND COALESCE(excluded_from_stats, false) = false
                GROUP BY service_id
                """,
                (tuple(self.ids), thirty_days_ago),
            )
            stats = {row[0]: (row[1], row[2]) for row in self.env.cr.fetchall()}
        for record in self:
            up, total = stats.get(record.id, (0, 0))
            record.uptime_30d = (up / total) * 100 if total else 100.0

    def _compute_health_check_count(self):
        counts = {}
        if self.ids:
            self.env.cr.execute(
                """
                SELECT service_id, COUNT(*)
                FROM hosting_health_check
                WHERE service_id IN %s
                GROUP BY service_id
                """,
                (tuple(self.ids),),
            )
            counts = dict(self.env.cr.fetchall())
        for record in self:
            record.health_check_count = counts.get(record.id, 0)

    def _compute_maintenance_schedule_count(self):
        today = fields.Date.today()
        for record in self:
            active_schedules = record.maintenance_schedule_ids.filtered(
                lambda s: s.active
            )
            record.maintenance_schedule_count = len(active_schedules)
            # Comparer next_due directement : is_overdue est un calculé stocké
            # que seul le cron quotidien rafraîchit, donc s'y fier ici ferait
            # afficher 0 tant que la tâche n'a pas été remarquée faite.
            record.maintenance_overdue_count = len(
                active_schedules.filtered(
                    lambda s: s.next_due and s.next_due < today
                )
            )

    def action_view_maintenance_schedules(self):
        """Voir les planifications de maintenance pour ce service."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": f"Maintenance - {self.name}",
            "res_model": "hosting.maintenance.schedule",
            "views": [[False, "list"], [False, "kanban"], [False, "form"]],
            "domain": [("service_id", "=", self.id)],
            "context": {"default_service_id": self.id},
        }

    def action_activate(self):
        """Activer le service et créer les planifications de maintenance depuis les modèles."""
        self.write({"state": "active"})
        for service in self:
            service._create_maintenance_from_templates()

    def action_acknowledge_disconnect(self):
        """Ouvrir l'assistant qui acte la non-reconduction du service."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Confirmer la non-reconduction",
            "res_model": "hosting.service.disconnect.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_service_id": self.id},
        }

    def action_suspend(self):
        """Suspendre le service."""
        self.write({"state": "suspended"})

    def action_cancel(self):
        """Annuler le service."""
        self.write({"state": "cancelled"})

    def action_set_draft(self):
        """Remettre le service en brouillon."""
        self.write({"state": "draft"})

    def action_view_contract(self):
        """Ouvrir le contrat lié."""
        self.ensure_one()
        if self.contract_id:
            return {
                "type": "ir.actions.act_window",
                "name": "Contrat",
                "res_model": "contract.contract",
                "views": [[False, "form"]],
                "res_id": self.contract_id.id,
            }

    def action_view_update_history(self):
        """Voir l'historique de mise à jour pour ce service."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": f"Historique de mise à jour - {self.name}",
            "res_model": "hosting.update.log",
            "views": [[False, "list"], [False, "form"]],
            "domain": [("service_id", "=", self.id)],
            "context": {"default_service_id": self.id},
        }

    def action_mark_updated(self):
        """Marquer le service comme mis à jour vers la version cible."""
        self.ensure_one()
        if self.target_version_id:
            self.env["hosting.update.log"].create({
                "service_id": self.id,
                "from_version_id": self.installed_version_id.id,
                "to_version_id": self.target_version_id.id,
                "notes": "Mise à jour manuelle marquée comme complétée.",
            })
            self.installed_version_id = self.target_version_id
            self.target_version_id = False

    def action_update_to_latest(self):
        """Marquer le service comme mis à jour vers la dernière version disponible
        selon sa politique (LTS ou stable courante)."""
        self.ensure_one()
        target_version = self._compute_update_target()
        if not self.update_available or not target_version:
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": "Aucune mise à jour disponible",
                    "message": "Ce service est déjà à la dernière version selon sa politique.",
                    "type": "info",
                },
            }

        Version = self.env["hosting.software.version"]
        latest_version_record = Version.search([
            ("software_id", "=", self.software_id.id),
            ("version", "=", target_version),
        ], limit=1)

        if not latest_version_record:
            # Only set release_date when target == software.latest_version
            # (we know its date); otherwise leave it blank.
            vals = {"software_id": self.software_id.id, "version": target_version}
            if target_version == self.software_id.latest_version:
                vals["release_date"] = self.software_id.latest_version_date
            latest_version_record = Version.create(vals)

        self.env["hosting.update.log"].create({
            "service_id": self.id,
            "from_version_id": self.installed_version_id.id,
            "to_version_id": latest_version_record.id,
            "notes": "Mise à jour manuelle vers la dernière version.",
        })

        old_version = self.installed_version_id.version or "inconnue"
        self.installed_version_id = latest_version_record

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "Version mise à jour",
                "message": f"Mise à jour de {old_version} vers {latest_version_record.version}",
                "type": "success",
            },
        }

    def _create_maintenance_from_templates(self):
        """Créer les planifications de maintenance depuis les modèles spécifiques au logiciel et par défaut."""
        self.ensure_one()
        MaintenanceSchedule = self.env["hosting.maintenance.schedule"]
        MaintenanceTemplate = self.env["hosting.maintenance.template"]
        today = fields.Date.today()

        # Obtenir les modèles spécifiques au logiciel
        templates = MaintenanceTemplate.search([
            ("software_id", "=", self.software_id.id),
            ("active", "=", True),
        ])

        # Obtenir aussi les modèles par défaut (aucun logiciel spécifié)
        default_templates = MaintenanceTemplate.search([
            ("software_id", "=", False),
            ("active", "=", True),
        ])

        all_templates = templates | default_templates

        created_schedules = []
        created_activities = []

        for template in all_templates:
            for line in template.line_ids.filtered(lambda l: l.active):
                # Vérifier si la planification existe déjà depuis cette ligne de modèle
                existing = MaintenanceSchedule.search([
                    ("service_id", "=", self.id),
                    ("template_line_id", "=", line.id),
                ], limit=1)
                if existing:
                    continue

                if line.frequency == "once":
                    due_date = today + timedelta(days=line.days_after_activation)

                    if line.create_activity:
                        activity_type = self.env.ref(
                            "mail.mail_activity_data_todo",
                            raise_if_not_found=False,
                        )
                        self.env["mail.activity"].create({
                            "res_model_id": self.env["ir.model"]._get_id("hosting.service"),
                            "res_id": self.id,
                            "activity_type_id": activity_type.id if activity_type else False,
                            "summary": line.name,
                            "note": line.instructions or f"Tâche de configuration pour {self.name}",
                            "date_deadline": due_date,
                            "user_id": self.user_id.id or self.env.user.id,
                        })
                        created_activities.append(line.name)

                    continue

                # Créer la planification de maintenance récurrente
                schedule_vals = {
                    "name": line.name,
                    "service_id": self.id,
                    "maintenance_type": line.maintenance_type,
                    "frequency": line.frequency,
                    "instructions": line.instructions,
                    "user_id": self.user_id.id or self.env.user.id,
                    "template_line_id": line.id,
                    "last_performed": today,
                }
                schedule = MaintenanceSchedule.create(schedule_vals)
                created_schedules.append(schedule.name)

        # Journaliser la création
        if created_schedules or created_activities:
            message_parts = []
            if created_schedules:
                message_parts.append(
                    f"<strong>Planifications de maintenance créées :</strong><ul>"
                    + "".join(f"<li>{_esc(name)}</li>" for name in created_schedules)
                    + "</ul>"
                )
            if created_activities:
                message_parts.append(
                    f"<strong>Activités de configuration créées :</strong><ul>"
                    + "".join(f"<li>{_esc(name)}</li>" for name in created_activities)
                    + "</ul>"
                )
            self.message_post(
                body="".join(message_parts),
                message_type="notification",
            )

    def action_apply_maintenance_templates(self):
        """Appliquer manuellement les modèles de maintenance à ce service."""
        self.ensure_one()
        self._create_maintenance_from_templates()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "Modèles appliqués",
                "message": "Les planifications de maintenance ont été créées depuis les modèles.",
                "type": "success",
            },
        }

    @api.model
    def _cron_check_expirations(self):
        """Tâche planifiée pour créer des activités pour les services expirant bientôt."""
        warning_days = int(
            self.env["ir.config_parameter"]
            .sudo()
            .get_param("hosting.expiration_warning_days", "90")
        )
        today = fields.Date.today()
        warning_date = today + timedelta(days=warning_days)

        services = self.search([
            ("state", "=", "active"),
            ("date_expiration", "<=", warning_date),
            ("date_expiration", ">=", today),
            ("expiration_activity_created", "=", False),
        ])

        activity_type = self.env.ref(
            "hosting_management.mail_activity_type_hosting_expiration",
            raise_if_not_found=False,
        )

        for service in services:
            self.env["mail.activity"].create({
                "res_model_id": self.env["ir.model"]._get_id("hosting.service"),
                "res_id": service.id,
                "activity_type_id": activity_type.id if activity_type else False,
                "summary": f"Le service expire dans {service.days_until_expiration} jours",
                "note": f"Le service d'hébergement « {service.name} » pour {service.partner_id.name} "
                        f"expirera le {service.date_expiration}. Veuillez contacter le client "
                        f"pour discuter du renouvellement.",
                "date_deadline": service.date_expiration,
                "user_id": service.user_id.id or self.env.user.id,
            })
            service.expiration_activity_created = True

    @api.model
    def _cron_auto_expire(self):
        """Tâche planifiée pour expirer automatiquement les services en retard."""
        today = fields.Date.today()
        services = self.search([
            ("state", "in", ("active", "to_disconnect")),
            ("date_expiration", "<", today),
        ])
        services.write({"state": "expired"})

    @api.model
    def _is_in_maintenance_window(self):
        """Vérifier si l'heure actuelle est dans la fenêtre de maintenance configurée.

        Returns:
            bool: True si actuellement dans la fenêtre de maintenance, False sinon
        """
        ICP = self.env["ir.config_parameter"].sudo()

        maintenance_enabled = ICP.get_param(
            "hosting.maintenance_window_enabled", "True"
        )
        if maintenance_enabled.lower() not in ("true", "1"):
            return False

        try:
            start_hour = float(ICP.get_param("hosting.maintenance_start_hour", "1.5"))
            end_hour = float(ICP.get_param("hosting.maintenance_end_hour", "2.5"))
        except (ValueError, TypeError):
            _logger.warning("Configuration de fenêtre de maintenance invalide, utilisation des valeurs par défaut")
            start_hour = 1.5
            end_hour = 2.5

        timezone_str = ICP.get_param("hosting.maintenance_timezone", "America/Toronto")

        from datetime import datetime

        if pytz:
            try:
                tz = pytz.timezone(timezone_str)
                now = datetime.now(tz)
            except Exception:
                _logger.warning(
                    "Fuseau horaire invalide %s, retour à UTC", timezone_str
                )
                now = datetime.utcnow()
        else:
            _logger.warning("pytz non disponible, utilisation de UTC pour la fenêtre de maintenance")
            now = datetime.utcnow()

        current_hour = now.hour + (now.minute / 60.0)

        if start_hour <= end_hour:
            in_window = start_hour <= current_hour < end_hour
        else:
            in_window = current_hour >= start_hour or current_hour < end_hour

        if in_window:
            _logger.info(
                "Actuellement dans la fenêtre de maintenance (%s à %s %s), alertes supprimées",
                self._format_hour(start_hour),
                self._format_hour(end_hour),
                timezone_str,
            )

        return in_window

    @staticmethod
    def _format_hour(decimal_hour):
        """Convertir une heure décimale au format HH:MM."""
        hours = int(decimal_hour)
        minutes = int((decimal_hour - hours) * 60)
        return f"{hours:02d}:{minutes:02d}"

    @staticmethod
    def _do_health_check(requests_mod, url, accepted_codes=None):
        """Execute a single HTTP health check.

        Returns (status, response_time_ms, http_status_code, error_message).
        ``accepted_codes`` is an optional set of HTTP status codes that should
        be treated as "up" even though they are >= 400.
        """
        try:
            response = requests_mod.get(
                url,
                timeout=10,
                allow_redirects=True,
                headers={"User-Agent": "Odoo-Hosting-Health-Check/1.0"},
            )
            sc = response.status_code
            if sc < 400 or (accepted_codes and sc in accepted_codes):
                status = "up"
            else:
                status = "degraded"
            return status, int(response.elapsed.total_seconds() * 1000), sc, None
        except requests_mod.Timeout:
            return "timeout", None, None, "La requête a expiré après 10 secondes"
        except requests_mod.RequestException as e:
            return "down", None, None, str(e)[:500]
        except Exception as e:
            return "down", None, None, f"Erreur inattendue : {str(e)[:450]}"

    @api.model
    def _cron_health_check(self):
        """Vérifier la santé de tous les services actifs avec URL."""
        try:
            import requests
        except ImportError:
            _logger.warning("Bibliothèque requests non installée, vérifications de santé ignorées")
            return

        services = self.search([
            ("state", "=", "active"),
            ("server_url", "!=", False),
        ])

        response_time_threshold = int(
            self.env["ir.config_parameter"]
            .sudo()
            .get_param("hosting.response_time_threshold_ms", "5000")
        )
        retry_count = int(
            self.env["ir.config_parameter"]
            .sudo()
            .get_param("hosting.health_alert_threshold", "3")
        )
        retry_delay = 10.0 / max(retry_count, 2)  # spread retries within 10s
        # Cross-cycle flap dampening: only page once a failure has persisted
        # across this many consecutive cron cycles. Defends against transient
        # bursts (e.g. a mailing-blast open/click tracking storm that briefly
        # exhausts the DB pool and returns 500s for a few seconds).
        min_consecutive = max(int(
            self.env["ir.config_parameter"]
            .sudo()
            .get_param("hosting.health_alert_min_consecutive", "2")
        ), 1)

        services_now_down = []
        services_recovered = []
        services_slow = []

        for service in services:
            accepted_codes = set(service.accepted_http_code_ids.mapped("code"))
            status, response_time_ms, http_code, error_message = self._do_health_check(
                requests, service.server_url, accepted_codes or None,
            )

            # On failure, do rapid retries to confirm it's a real outage
            if status in ("down", "timeout", "degraded"):
                confirmed_down = True
                for attempt in range(1, retry_count):
                    time.sleep(retry_delay)
                    r_status, r_time, r_code, r_err = self._do_health_check(
                        requests, service.server_url,
                    )
                    if r_status == "up":
                        _logger.info(
                            "Service %s : échec initial mais retry %d/%d réussi, faux positif évité",
                            service.name, attempt, retry_count - 1,
                        )
                        confirmed_down = False
                        # Use the successful retry result
                        status, response_time_ms, http_code, error_message = r_status, r_time, r_code, r_err
                        break

                if confirmed_down:
                    _logger.warning(
                        "Service %s confirmé hors ligne après %d vérifications en ≤10s",
                        service.name, retry_count,
                    )

            # Record the final health check result
            check_vals = {"service_id": service.id, "status": status}
            if response_time_ms is not None:
                check_vals["response_time_ms"] = response_time_ms
            if http_code is not None:
                check_vals["http_status_code"] = http_code
            if error_message:
                check_vals["error_message"] = error_message
            self.env["hosting.health.check"].create(check_vals)

            # Alert logic — flap dampening across cron cycles. Only raise an
            # alert once the failure has persisted for ``min_consecutive``
            # consecutive checks (default 2). A transient blip recovers within
            # one cycle and never pages; a real sustained outage still alerts
            # within ~N cycles. The check created just above is the newest row,
            # so it counts toward the window.
            if status in ("down", "timeout", "degraded"):
                if not service.health_alert_active:
                    recent = self.env["hosting.health.check"].search(
                        [("service_id", "=", service.id)],
                        order="check_date desc, id desc",
                        limit=min_consecutive,
                    )
                    sustained = (
                        len(recent) >= min_consecutive
                        and all(r.status != "up" for r in recent)
                    )
                    if sustained:
                        service.write({"health_alert_active": True})
                        services_now_down.append({
                            "service": service,
                            "status": status,
                            "error": error_message,
                        })
            elif status == "up":
                if service.health_alert_active:
                    services_recovered.append({
                        "service": service,
                        "response_time_ms": response_time_ms,
                    })
                    service.write({"health_alert_active": False})

            if (status == "up" and response_time_ms
                    and response_time_ms > response_time_threshold):
                services_slow.append({
                    "service": service,
                    "response_time_ms": response_time_ms,
                    "threshold_ms": response_time_threshold,
                })

        if self._is_in_maintenance_window():
            if services_now_down:
                _logger.info(
                    "Fenêtre de maintenance : alerte HORS LIGNE supprimée pour %d service(s)",
                    len(services_now_down),
                )
            if services_recovered:
                _logger.info(
                    "Fenêtre de maintenance : alerte RÉTABLI supprimée pour %d service(s)",
                    len(services_recovered),
                )
            if services_slow:
                _logger.info(
                    "Fenêtre de maintenance : alerte LENT supprimée pour %d service(s)",
                    len(services_slow),
                )
        else:
            if services_now_down:
                self._send_health_alert_email(services_now_down, alert_type="down")
                self._send_ntfy_alert(services_now_down, alert_type="down")

            if services_recovered:
                self._send_health_alert_email(services_recovered, alert_type="recovered")
                self._send_ntfy_alert(services_recovered, alert_type="recovered")

            if services_slow:
                self._send_health_alert_email(services_slow, alert_type="slow")

    def _send_ntfy_alert(self, services_list, alert_type="down"):
        """Envoyer une notification push via ntfy pour les événements de santé.

        Args:
            services_list: Liste de dicts avec les informations de service
            alert_type: "down" ou "recovered"
        """
        count = len(services_list)
        if alert_type == "down":
            title = f"ALERTE : {count} service(s) hors ligne"
            priority = "urgent"
            tags = "rotating_light"
            lines = []
            for item in services_list:
                svc = item["service"]
                status = item.get("status", "down").upper()
                error = item.get("error") or ""
                line = f"- {svc.name} ({svc.partner_id.name or 'N/D'}) [{status}]"
                if error:
                    line += f" : {error[:80]}"
                lines.append(line)
        elif alert_type == "recovered":
            title = f"RÉTABLI : {count} service(s) de nouveau en ligne"
            priority = "default"
            tags = "white_check_mark"
            lines = []
            for item in services_list:
                svc = item["service"]
                rt = item.get("response_time_ms", 0)
                lines.append(f"- {svc.name} ({svc.partner_id.name or 'N/D'}) [{rt}ms]")
        else:
            return

        self.env["hosting.ntfy"].send(
            title=title,
            body="\n".join(lines),
            priority=priority,
            tags=tags,
        )

    def _send_health_alert_email(self, services_list, alert_type="down"):
        """Envoyer un courriel d'alerte pour les événements de santé.

        Args:
            services_list: Liste de dicts avec les informations de service
            alert_type: "down", "recovered" ou "slow"
        """
        from . import hosting_email_template as email_tpl

        alert_email = (
            self.env["ir.config_parameter"]
            .sudo()
            .get_param("hosting.health_alert_email", "")
        )

        if not alert_email:
            _logger.warning(
                "Aucun courriel d'alerte de santé configuré. Définir 'hosting.health_alert_email' "
                "dans les paramètres système pour recevoir les alertes."
            )
            return

        if alert_type == "down":
            subject = f"ALERTE : {len(services_list)} service(s) d'hébergement hors ligne"
            title = "Alerte de service"
            description = f"Les <strong>{len(services_list)}</strong> service(s) suivant(s) sont actuellement <strong>HORS LIGNE</strong> ou rencontrent des problèmes :"
            headers = ["Service", "Client", "URL", "État", "Erreur"]
        elif alert_type == "recovered":
            subject = f"RÉTABLI : {len(services_list)} service(s) d'hébergement de nouveau en ligne"
            title = "Rétablissement de service"
            description = f"Les <strong>{len(services_list)}</strong> service(s) suivant(s) ont été <strong>RÉTABLIS</strong> et sont maintenant opérationnels :"
            headers = ["Service", "Client", "URL", "Temps de réponse"]
        elif alert_type == "slow":
            subject = f"AVERTISSEMENT : {len(services_list)} service(s) d'hébergement lent(s)"
            title = "Avertissement de performance"
            description = f"Les <strong>{len(services_list)}</strong> service(s) suivant(s) connaissent des <strong>TEMPS DE RÉPONSE LENTS</strong> :"
            headers = ["Service", "Client", "URL", "Temps de réponse", "Seuil"]
        else:
            return

        table_rows = []
        for item in services_list:
            service = item["service"]
            safe_url = _esc(service.server_url or "")
            url_link = f'<a href="{safe_url}" style="color:#29abe2; text-decoration:none;">{safe_url}</a>'
            svc_cell = f"{_esc(service.name)}<br/><span style='color:#6B7280; font-size:12px;'>{_esc(service.code)}</span>"
            partner_cell = _esc(service.partner_id.name or "")

            if alert_type == "down":
                status = item.get("status", "DOWN").upper()
                status_badge = email_tpl.get_status_badge(status, "small")
                error = item.get("error") or "N/D"
                safe_error = _esc(error[:50]) + ("..." if len(error) > 50 else "")
                row = [
                    svc_cell,
                    partner_cell,
                    url_link,
                    status_badge,
                    f"<span style='font-size:13px; color:#6B7280;'>{safe_error}</span>",
                ]
            elif alert_type == "recovered":
                response_time = item.get("response_time_ms", 0)
                row = [
                    svc_cell,
                    partner_cell,
                    url_link,
                    f"<span style='color:#198754; font-weight:600;'>{response_time} ms</span>",
                ]
            elif alert_type == "slow":
                response_time = item.get("response_time_ms", 0)
                threshold = item.get("threshold_ms", 5000)
                row = [
                    svc_cell,
                    partner_cell,
                    url_link,
                    f"<span style='color:#ffc107; font-weight:600;'>{response_time} ms</span>",
                    f"{threshold} ms",
                ]

            table_rows.append(row)

        content_parts = [
            f'<p style="font-family:\'Lexend\',\'Segoe UI\',Arial,sans-serif; font-size:16px; line-height:26px; color:#374151; margin:0 0 20px 0;">{description}</p>',
            email_tpl.get_data_table(headers, table_rows, company=self.env.company),
            email_tpl.get_contact_footer(),
        ]

        content = "".join(content_parts)
        body_html = email_tpl.get_email_wrapper(title, content, alert_type, company=self.env.company)

        try:
            mail_values = {
                "subject": subject,
                "email_from": self.env.company.email or self.env.user.email_formatted,
                "email_to": alert_email,
                "body_html": body_html,
                "auto_delete": True,
            }
            mail = self.env["mail.mail"].sudo().create(mail_values)
            mail.send()
            _logger.info("Courriel d'alerte de santé (%s) envoyé à %s pour %d service(s)",
                        alert_type, alert_email, len(services_list))
        except Exception as e:
            _logger.exception("Échec de l'envoi du courriel d'alerte de santé : %s", str(e))

    def action_view_health_history(self):
        """Voir l'historique des vérifications de santé pour ce service."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": f"Historique de santé - {self.name}",
            "res_model": "hosting.health.check",
            "views": [[False, "list"], [False, "form"]],
            "domain": [("service_id", "=", self.id)],
            "context": {"default_service_id": self.id},
        }

    def action_run_health_check(self):
        """Exécuter manuellement la vérification de santé pour ce service."""
        self.ensure_one()
        if not self.server_url:
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": "Aucune URL",
                    "message": "Ce service n'a pas d'URL configurée pour les vérifications de santé.",
                    "type": "warning",
                },
            }

        try:
            import requests
        except ImportError:
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": "Bibliothèque manquante",
                    "message": "La bibliothèque requests n'est pas installée.",
                    "type": "danger",
                },
            }

        try:
            response = requests.get(
                self.server_url,
                timeout=10,
                allow_redirects=True,
                headers={"User-Agent": "Odoo-Hosting-Health-Check/1.0"},
            )
            accepted_codes = set(self.accepted_http_code_ids.mapped("code"))
            sc = response.status_code
            if sc < 400 or (accepted_codes and sc in accepted_codes):
                status = "up"
            else:
                status = "degraded"
            self.env["hosting.health.check"].create({
                "service_id": self.id,
                "status": status,
                "response_time_ms": int(response.elapsed.total_seconds() * 1000),
                "http_status_code": response.status_code,
            })
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": "Vérification de santé terminée",
                    "message": f"État : {status.upper()}, Réponse : {response.status_code}",
                    "type": "success" if status == "up" else "warning",
                },
            }
        except requests.Timeout:
            self.env["hosting.health.check"].create({
                "service_id": self.id,
                "status": "timeout",
                "error_message": "La requête a expiré après 10 secondes",
            })
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": "Vérification de santé terminée",
                    "message": "Délai du service dépassé",
                    "type": "danger",
                },
            }
        except Exception as e:
            self.env["hosting.health.check"].create({
                "service_id": self.id,
                "status": "down",
                "error_message": str(e)[:500],
            })
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": "Vérification de santé terminée",
                    "message": f"Service hors ligne : {str(e)[:100]}",
                    "type": "danger",
                },
            }

    @api.model
    def _cron_check_docker_versions(self):
        """Tâche planifiée pour les vérifications de version Docker."""
        results = self._check_docker_versions()
        _logger.info(
            "Vérification de version Docker terminée : %d vérifiés, %d mis à jour, %d erreurs",
            results["checked"],
            results["updated"],
            len(results["errors"]),
        )
        if results["errors"]:
            for error in results["errors"][:10]:
                _logger.warning("Erreur de vérification de version Docker : %s", error)
        return results

    def action_check_docker_version(self):
        """Déclencher manuellement la vérification de version Docker pour ce service."""
        self.ensure_one()
        if not self.docker_host or not self.docker_container:
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": "Configuration manquante",
                    "message": "L'hôte Docker et le conteneur doivent être configurés pour vérifier la version.",
                    "type": "warning",
                },
            }

        results = self._check_docker_versions()
        if results["errors"]:
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": "Échec de la vérification de version",
                    "message": results["errors"][0] if results["errors"] else "Erreur inconnue",
                    "type": "danger",
                },
            }
        elif results["updated"]:
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": "Version mise à jour",
                    "message": f"Version installée mise à jour vers {self.installed_version_id.version}",
                    "type": "success",
                },
            }
        else:
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": "Vérification de version terminée",
                    "message": f"Version actuelle : {self.installed_version_id.version or 'Inconnue'}",
                    "type": "info",
                },
            }

    @api.model
    def _check_docker_versions(self):
        """Vérifier les versions des conteneurs Docker.

        Utilise le socket Docker local si disponible, sinon SSH vers les hôtes distants.
        Retourne un dict avec les résultats.
        """
        import os
        import shlex
        import subprocess

        results = {
            "checked": 0,
            "updated": 0,
            "errors": [],
        }

        has_local_docker = os.path.exists("/var/run/docker.sock")

        services = self.search([
            ("docker_host", "!=", False),
            ("docker_container", "!=", False),
            ("state", "=", "active"),
        ])

        if not services:
            return results

        # Regrouper les services par hôte (hostname → {services, ssh_user})
        hosts = {}
        for service in services:
            host = service.docker_host
            if host not in hosts:
                ssh_user = service.server_id.ssh_user if service.server_id else "root"
                hosts[host] = {"services": [], "ssh_user": ssh_user}
            hosts[host]["services"].append(service)

        for host, host_info in hosts.items():
            host_services = host_info["services"]
            ssh_user = host_info["ssh_user"]
            container_names = [s.docker_container for s in host_services]

            try:
                if has_local_docker:
                    cmd = [
                        "docker", "inspect",
                        "--format", "{{.Name}}|{{.Config.Image}}",
                    ] + container_names
                    result = subprocess.run(
                        cmd,
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                else:
                    # Shell-quote each container name to prevent command injection
                    containers_arg = " ".join(
                        shlex.quote(name) for name in container_names
                    )
                    cmd = [
                        "ssh", "-o", "StrictHostKeyChecking=yes",
                        "-o", "ConnectTimeout=10",
                        f"{shlex.quote(ssh_user)}@{shlex.quote(host)}",
                        f"docker inspect --format '{{{{.Name}}}}|{{{{.Config.Image}}}}' {containers_arg} 2>/dev/null || true"
                    ]
                    result = subprocess.run(
                        cmd,
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )

                if result.returncode != 0 and result.stderr:
                    method = "Docker" if has_local_docker else "SSH"
                    results["errors"].append(f"{host} : erreur {method} - {result.stderr[:100]}")
                    continue

                version_map = {}
                for line in result.stdout.strip().split("\n"):
                    if "|" in line:
                        container_name, image = line.split("|", 1)
                        container_name = container_name.lstrip("/")
                        if ":" in image:
                            version = image.split(":")[-1]
                        else:
                            version = "latest"
                        version_map[container_name] = version

                for service in host_services:
                    results["checked"] += 1
                    container_name = service.docker_container

                    if container_name not in version_map:
                        results["errors"].append(
                            f"{service.name} : conteneur « {container_name} » introuvable sur {host}"
                        )
                        continue

                    docker_version = version_map[container_name]

                    skip_tags = {"latest", "stable", "edge", "dev", "beta", "alpha", "nightly"}
                    if docker_version.lower() in skip_tags:
                        continue

                    clean_version = docker_version
                    if clean_version.startswith("version-"):
                        clean_version = clean_version[8:]
                    elif clean_version.startswith("v") and len(clean_version) > 1 and clean_version[1].isdigit():
                        clean_version = clean_version[1:]

                    Version = self.env["hosting.software.version"]
                    version_record = Version.search([
                        ("software_id", "=", service.software_id.id),
                        ("version", "=", clean_version),
                    ], limit=1)

                    if not version_record:
                        version_record = Version.create({
                            "software_id": service.software_id.id,
                            "version": clean_version,
                        })

                    if service.installed_version_id != version_record:
                        old_version_id = service.installed_version_id.id
                        service.write({"installed_version_id": version_record.id})

                        self.env["hosting.update.log"].create({
                            "service_id": service.id,
                            "from_version_id": old_version_id,
                            "to_version_id": version_record.id,
                            "notes": f"Détection automatique via inspection Docker sur {host}",
                            "success": True,
                        })

                        results["updated"] += 1
                        _logger.info(
                            "Mise à jour de %s vers la version %s (depuis Docker)",
                            service.name, clean_version
                        )

            except subprocess.TimeoutExpired:
                results["errors"].append(f"{host} : délai de connexion SSH dépassé")
            except Exception as e:
                results["errors"].append(f"{host} : {str(e)[:100]}")
                _logger.exception("Erreur lors de la vérification des versions Docker sur %s", host)

        return results
