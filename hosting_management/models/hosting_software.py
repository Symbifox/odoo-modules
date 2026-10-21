# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import logging
import re

import requests

from odoo import api, fields, models

from .hosting_lang import reader_env

_logger = logging.getLogger(__name__)


class HostingSoftware(models.Model):
    _name = "hosting.software"
    _description = "Hosting software catalog"
    _order = "name"
    _inherit = ["mail.thread", "mail.activity.mixin"]

    name = fields.Char(
        string="Software name",
        required=True,
        translate=True,
        tracking=True,
    )
    code = fields.Char(
        string="Short code",
        required=True,
        help="Short identifier (e.g. NC for Nextcloud, OD for Odoo)",
    )
    software_type = fields.Selection(
        selection=[
            ("self_hosted", "Self-hosted"),
            ("saas", "SaaS"),
            ("managed", "Managed"),
        ],
        string="Type",
        default="self_hosted",
        required=True,
        tracking=True,
    )
    versionless = fields.Boolean(
        string="Versionless",
        tracking=True,
        help="The software does not publish a version number (SaaS, "
             "probe, internal tool). Its services are not listed among "
             "the versions to fill in.",
    )
    latest_version = fields.Char(
        string="Latest version",
        tracking=True,
    )
    latest_version_date = fields.Date(
        string="Latest version date",
        help="Release date of the latest version",
    )
    update_available = fields.Boolean(
        string="Available updates",
        compute="_compute_update_available",
        store=True,
        help="True if services need updates",
    )
    github_url = fields.Char(
        string="GitHub URL",
        help="Repository URL for automatic version checks",
    )
    docker_image = fields.Char(
        string="Docker image",
        help="Docker image reference (e.g. nextcloud:latest)",
    )

    # Configuration de vérification de version
    version_check_method = fields.Selection(
        selection=[
            ("none", "Manual"),
            ("github", "GitHub releases"),
            ("docker_hub", "Docker Hub"),
            ("gitlab", "GitLab releases"),
        ],
        string="Version check method",
        default="none",
        help="How to check for new versions automatically",
    )
    github_repo = fields.Char(
        string="GitHub repository",
        help="Repository in \"owner/repo\" format (e.g. \"nextcloud/server\")",
    )
    docker_hub_repo = fields.Char(
        string="Docker Hub repository",
        help="Repository in \"owner/image\" format, or \"library/image\" "
             "for official images (e.g. \"nextcloud\" or "
             "\"library/postgres\")",
    )
    gitlab_repo = fields.Char(
        string="GitLab repository",
        help="Full URL of the GitLab project (e.g. "
             "\"https://gitlab.com/cryptpad/cryptpad\")",
    )
    version_regex = fields.Char(
        string="Version regular expression",
        default=r"v?(\d+\.\d+\.?\d*)",
        help="Regex pattern to extract the version number from tags. Use "
             "a capture group for the version.",
    )
    version_prefix = fields.Char(
        string="Version prefix",
        help="Prefix to strip from version tags (e.g. \"v\", \"release-\")",
    )
    last_version_check = fields.Datetime(
        string="Last version check",
        readonly=True,
    )
    version_check_error = fields.Char(
        string="Last check error",
        readonly=True,
    )

    image = fields.Image(
        string="Logo",
        max_width=256,
        max_height=256,
    )
    version_ids = fields.One2many(
        comodel_name="hosting.software.version",
        inverse_name="software_id",
        string="Versions",
    )
    service_ids = fields.One2many(
        comodel_name="hosting.service",
        inverse_name="software_id",
        string="Services",
    )
    service_count = fields.Integer(
        string="Service count",
        compute="_compute_service_count",
    )
    maintenance_template_ids = fields.One2many(
        comodel_name="hosting.maintenance.template",
        inverse_name="software_id",
        string="Maintenance templates",
    )
    active = fields.Boolean(
        string="Active",
        default=True,
    )
    notes = fields.Html(
        string="Notes",
        translate=True,
    )

    _sql_constraints = [
        ("code_uniq", "UNIQUE(code)", "The software code must be unique!"),
    ]

    @api.depends("service_ids")
    def _compute_service_count(self):
        for record in self:
            record.service_count = len(record.service_ids)

    @api.depends("service_ids.update_available")
    def _compute_update_available(self):
        for record in self:
            record.update_available = any(
                service.update_available for service in record.service_ids
            )

    def action_view_services(self):
        """Ouvrir les services utilisant ce logiciel."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Services - %s", self.name),
            "res_model": "hosting.service",
            "views": [[False, "list"], [False, "form"], [False, "kanban"]],
            "domain": [("software_id", "=", self.id)],
            "context": {"default_software_id": self.id},
        }

    def action_view_versions(self):
        """Ouvrir l'historique des versions pour ce logiciel."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Versions - %s", self.name),
            "res_model": "hosting.software.version",
            "views": [[False, "list"], [False, "form"]],
            "domain": [("software_id", "=", self.id)],
            "context": {"default_software_id": self.id},
        }

    def action_check_version(self):
        """Déclencher manuellement la vérification de version pour ce logiciel."""
        self.ensure_one()
        self._check_version()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": self.env._("Version check completed"),
                "message": self.env._(
                    "Latest version: %s", self.latest_version or self.env._("Not found")
                )
                if not self.version_check_error
                else self.env._("Error: %s", self.version_check_error),
                "type": "success" if not self.version_check_error else "warning",
                "sticky": False,
            },
        }

    def _version_error_env(self):
        """Env des messages d'échec écrits dans `version_check_error` (stocké,
        relu par l'équipe d'hébergement) : langue de la société."""
        return reader_env(self.env)

    def _check_version(self):
        """Vérifier les nouvelles versions selon la méthode configurée."""
        self.ensure_one()
        self.version_check_error = False

        try:
            if self.version_check_method == "github":
                self._check_github_version()
            elif self.version_check_method == "docker_hub":
                self._check_docker_hub_version()
            elif self.version_check_method == "gitlab":
                self._check_gitlab_version()

            self.last_version_check = fields.Datetime.now()
        except Exception as e:
            # Texte STOCKÉ, lu au formulaire et au tableau de bord : les messages
            # de ce module y arrivent dans la langue de la société (voir
            # `_version_error_env`), ceux des bibliothèques restent techniques.
            self.version_check_error = str(e)[:255]
            _logger.warning(
                "Version check failed for %s: %s", self.name, str(e)
            )

    def _check_github_version(self):
        """Vérifier l'API des versions GitHub pour la dernière version."""
        if not self.github_repo:
            raise ValueError(self._version_error_env()._("GitHub repository not configured"))

        headers = {"Accept": "application/vnd.github.v3+json"}

        # 🔴 NE PAS se fier à `/releases/latest` seul.
        #
        # GitHub y met la parution NON-préversion la plus RÉCEMMENT PUBLIÉE, ce
        # qui n'est pas la plus haute quand un projet entretient plusieurs
        # branches en parallèle et les publie le même jour. Nextcloud publie
        # 32.0.15, 33.0.9 et 34.0.4 le 2026-09-10 : `/releases/latest` rend
        # 32.0.15, la branche la PLUS ANCIENNE.
        #
        # Conséquence : le traqueur portait 32.0.15 pendant que les Nextcloud
        # suivis tournaient en 34.0.4.
        # Une version installée plus récente que la cible ne déclenche jamais
        # `update_available` — les mises à jour Nextcloud étaient donc
        # INVISIBLES, sans la moindre erreur de vérification pour le dire.
        # Collabora avait la même forme, son `/releases/latest` datant de
        # décembre 2025 alors que la 26.04 était installée.
        #
        # On classe donc les parutions récentes par semver, comme
        # `_check_docker_hub_version` le fait déjà pour les tags Docker Hub.
        # `/releases/latest` ne sert plus que de repli.
        url = f"https://api.github.com/repos/{self.github_repo}/releases"
        response = requests.get(
            url, headers=headers, params={"per_page": 30}, timeout=10
        )
        if response.status_code == 200:
            parutions = response.json() or []
            stables = [
                r for r in parutions
                if not r.get("prerelease") and not r.get("draft")
            ]
            best = self._pick_highest_semver_tag(
                r.get("tag_name", "") for r in stables
            )
            if best:
                release_date = None
                for r in stables:
                    if self._extract_version(r.get("tag_name", "")) == best:
                        publie = r.get("published_at", "")
                        if publie:
                            release_date = fields.Date.to_date(publie[:10])
                        break
                self._update_latest_version(best, release_date)
                return

        url = f"https://api.github.com/repos/{self.github_repo}/releases/latest"
        response = requests.get(url, headers=headers, timeout=10)

        if response.status_code == 404:
            # Try tags instead of releases — pick highest semver, skip pre-releases
            url = f"https://api.github.com/repos/{self.github_repo}/tags"
            response = requests.get(url, headers=headers, timeout=10)
            if response.status_code == 200:
                tags = response.json() or []
                best = self._pick_highest_semver_tag(t.get("name", "") for t in tags)
                if best:
                    self._update_latest_version(best)
                    return
            raise ValueError(self._version_error_env()._(
                "No releases or tags found for %s", self.github_repo))

        response.raise_for_status()
        data = response.json()

        # Skip if GitHub flagged this as a pre-release (defense in depth — the
        # `/releases/latest` endpoint already filters them, but some repos
        # publish pre-releases to that slot when they have no stable release).
        if data.get("prerelease"):
            raise ValueError(self._version_error_env()._(
                "Latest GitHub release for %s is a pre-release", self.github_repo
            ))

        tag_name = data.get("tag_name", "")
        if self._is_floating_or_prerelease(tag_name):
            raise ValueError(self._version_error_env()._(
                "GitHub latest tag %s is floating or pre-release", repr(tag_name)
            ))
        version = self._extract_version(tag_name)
        if version:
            published_at = data.get("published_at", "")
            release_date = None
            if published_at:
                release_date = fields.Date.to_date(published_at[:10])
            self._update_latest_version(version, release_date)

    def _pick_highest_semver_tag(self, tag_names):
        """Pick the highest parseable semver tag, skipping pre-releases."""
        candidates = []
        for name in tag_names:
            if not name or self._is_floating_or_prerelease(name):
                continue
            version = self._extract_version(name)
            if not version:
                continue
            parsed = self._parse_version_tuple(version)
            if parsed:
                candidates.append((parsed, version))
        if not candidates:
            return None
        candidates.sort(reverse=True)
        return candidates[0][1]

    def _check_docker_hub_version(self):
        """Vérifier l'API Docker Hub pour la dernière étiquette de version.

        Trie les tags par version semver parsée (DESC), pas par date d'upload —
        sinon un hotfix sur une vieille branche peut être remonté comme
        « dernière version » (ex. CubeBackup 1.2.15 hotfix vs 1.2.30 stable).
        """
        if not self.docker_hub_repo:
            raise ValueError(self._version_error_env()._("Docker Hub repository not configured"))

        repo = self.docker_hub_repo
        # Handle official images (no slash)
        if "/" not in repo:
            repo = f"library/{repo}"

        url = f"https://hub.docker.com/v2/repositories/{repo}/tags"
        params = {"page_size": 100, "ordering": "last_updated"}

        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()

        tags = data.get("results", [])
        if not tags:
            raise ValueError(self._version_error_env()._(
                "No tags found for %s", self.docker_hub_repo))

        # Collect (parsed_version, version_string, release_date) candidates.
        # Skip floating tags and pre-release suffixes.
        candidates = []
        for tag_info in tags:
            tag_name = tag_info.get("name", "")
            if self._is_floating_or_prerelease(tag_name):
                continue
            version = self._extract_version(tag_name)
            if not version:
                continue
            parsed = self._parse_version_tuple(version)
            if not parsed:
                continue
            last_updated = tag_info.get("last_updated", "")
            release_date = (
                fields.Date.to_date(last_updated[:10]) if last_updated else None
            )
            candidates.append((parsed, version, release_date))

        if not candidates:
            raise ValueError(self._version_error_env()._(
                "No version tags found for %s", self.docker_hub_repo))

        # Highest parsed semver wins
        candidates.sort(reverse=True)
        _parsed, version, release_date = candidates[0]
        self._update_latest_version(version, release_date)

    @staticmethod
    def _is_floating_or_prerelease(tag_name):
        """Reject floating tags (latest/stable/edge) and pre-release suffixes."""
        if not tag_name:
            return True
        lower = tag_name.lower().strip()
        # Floating tags as full names
        if lower in {"latest", "stable", "edge", "dev", "beta", "alpha", "rc",
                     "nightly", "main", "master", "head"}:
            return True
        # Pre-release / dev suffixes anywhere after a separator
        if re.search(
            r"(^|[-_.])(rc|beta|alpha|pre|dev|nightly|edge|snapshot)\d*([-_.+]|$)",
            lower,
        ):
            return True
        return False

    @staticmethod
    def _parse_version_tuple(version_str):
        """Parse a clean version string into a comparable tuple.

        Handles: '1.2.30', 'v1.2.3', '25.04.8.1'. Returns None if no integer
        components are present (e.g. for 'latest' or empty).
        """
        if not version_str:
            return None
        v = version_str.lstrip("vV")
        parts = re.split(r"[.\-]", v)
        out = []
        for p in parts:
            if p.isdigit():
                out.append(int(p))
            elif p.lstrip("-").isdigit():
                out.append(int(p))
            else:
                # Non-numeric component (e.g. 'rc1') — bail to keep ordering pure
                return None
        return tuple(out) if out else None

    def _check_gitlab_version(self):
        """Vérifier l'API des versions GitLab pour la dernière version."""
        if not self.gitlab_repo:
            raise ValueError(self._version_error_env()._("GitLab repository not configured"))

        # Parse GitLab URL to extract project path
        import urllib.parse

        parsed = urllib.parse.urlparse(self.gitlab_repo)
        gitlab_host = f"{parsed.scheme}://{parsed.netloc}"
        project_path = parsed.path.strip("/")
        encoded_path = urllib.parse.quote(project_path, safe="")

        url = f"{gitlab_host}/api/v4/projects/{encoded_path}/releases"

        response = requests.get(url, timeout=10)
        if response.status_code == 404:
            # Try tags — pick highest semver, skip pre-releases
            url = f"{gitlab_host}/api/v4/projects/{encoded_path}/repository/tags"
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                tags = response.json() or []
                best = self._pick_highest_semver_tag(t.get("name", "") for t in tags)
                if best:
                    self._update_latest_version(best)
                    return
            raise ValueError(self._version_error_env()._(
                "No releases or tags found for %s", self.gitlab_repo))

        response.raise_for_status()
        data = response.json()

        if not data:
            raise ValueError(self._version_error_env()._(
                "No releases found for %s", self.gitlab_repo))

        # Skip pre-release slots; pick highest stable semver among returned releases
        candidates = []
        for release in data:
            tag_name = release.get("tag_name", "")
            if self._is_floating_or_prerelease(tag_name):
                continue
            version = self._extract_version(tag_name)
            if not version:
                continue
            parsed = self._parse_version_tuple(version)
            if not parsed:
                continue
            released_at = release.get("released_at", "")
            release_date = (
                fields.Date.to_date(released_at[:10]) if released_at else None
            )
            candidates.append((parsed, version, release_date))

        if not candidates:
            raise ValueError(self._version_error_env()._(
                "No stable releases found for %s", self.gitlab_repo
            ))
        candidates.sort(reverse=True)
        _parsed, version, release_date = candidates[0]
        self._update_latest_version(version, release_date)

    def _extract_version(self, tag_name):
        """Extraire le numéro de version du nom d'étiquette via le regex configuré.

        Le regex est appliqué sur les 500 premiers caractères du tag pour borner
        le coût. La protection ReDoS via `signal.SIGALRM` a été retirée — elle
        levait `ValueError: signal only works in main thread of the main interpreter`
        dans les workers HTTP threadés d'Odoo, ce qui faisait silencieusement
        échouer chaque vérification de version. Le regex est sous notre contrôle
        (champ admin), pas user input, donc le risque ReDoS est acceptable.
        """
        if not tag_name:
            return None

        # Strip prefix if configured
        if self.version_prefix and tag_name.startswith(self.version_prefix):
            tag_name = tag_name[len(self.version_prefix):]

        if self.version_regex:
            try:
                match = re.search(self.version_regex, tag_name[:500])
                if match:
                    return match.group(1) if match.groups() else match.group(0)
            except re.error as exc:
                _logger.warning(
                    "Regex error for %s (%s): %s",
                    self.name, self.version_regex, exc,
                )

        return tag_name

    def _update_latest_version(self, version, release_date=None):
        """Mettre à jour la dernière version et créer un enregistrement de version si nouveau."""
        if self.latest_version != version:
            vals = {"latest_version": version}
            if release_date:
                vals["latest_version_date"] = release_date
            self.write(vals)

            # Create version record if it doesn't exist
            existing = self.env["hosting.software.version"].search([
                ("software_id", "=", self.id),
                ("version", "=", version),
            ], limit=1)
            if not existing:
                self.env["hosting.software.version"].create({
                    "software_id": self.id,
                    "version": version,
                    "release_date": release_date,
                    "support_status": "supported",
                })

            # Trigger recomputation of update_available on services
            self.service_ids._compute_update_available()

    def action_check_versions_now(self):
        """Manuel : déclencher la vérification de version pour les enregistrements
        sélectionnés. Public — accessible via XML-RPC pour les scripts d'audit.
        """
        for software in self:
            try:
                software._check_version()
            except Exception as e:
                _logger.warning("Version check failed for %s: %s", software.name, e)
        return True

    @api.model
    def _cron_check_versions(self):
        """Tâche planifiée pour vérifier les nouvelles versions de tous les logiciels configurés."""
        software_to_check = self.search([
            ("version_check_method", "!=", "none"),
        ])
        updates = []
        for software in software_to_check:
            previous = software.latest_version
            try:
                software._check_version()
                # Commit after each check to avoid losing all progress on error
                self.env.cr.commit()
            except Exception as e:
                _logger.error(
                    "Version check cron failed for %s: %s", software.name, str(e)
                )
                self.env.cr.rollback()
                continue
            software.invalidate_recordset(["latest_version"])
            if software.latest_version and software.latest_version != previous:
                updates.append((software.name, previous or "?", software.latest_version))

        if updates:
            # Alerte lue par l'équipe d'hébergement : langue de la société.
            env = reader_env(self.env)
            body_lines = [f"- {name}: {old} → {new}" for name, old, new in updates[:15]]
            if len(updates) > 15:
                body_lines.append(env._("… and %s more", len(updates) - 15))
            self.env["hosting.ntfy"].send(
                title=env._("VERSIONS: %s new version(s) detected", len(updates)),
                body="\n".join(body_lines),
                priority="default",
                tags="package,arrow_up",
            )
