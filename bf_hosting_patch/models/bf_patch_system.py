# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""bf.patch.system — un système installé sur une machine du parc.

Le parc Blue Fox est presque tout en double amorçage. Une fiche
`hosting.endpoint` décrit donc une MACHINE (numéro de série, garantie, achat,
clé BitLocker, sièges de licence), et ce modèle-ci décrit une INSTALLATION :
son noyau, son gestionnaire de paquets, ses paquets en attente, sa fin de
support.

⚠️ Le `/etc/machine-id` identifie l'installation, PAS la machine : chaque
système en génère un. C'est pour ça qu'il vit ici et non sur la fiche du parc,
où il aurait fabriqué le doublon qu'il devait empêcher.
"""

import hashlib
import logging
import secrets

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)

# Deux fois la cadence de relevé attendue (quotidienne). Assez pour absorber un
# portable fermé la fin de semaine, pas assez pour oublier une machine. Un
# système de double amorçage qu'on ne démarre jamais bascule donc en « muet »,
# et c'est voulu : il continue de vieillir.
STALE_AFTER_HOURS = 48

DISK_WARN_PCT = 90

# Du pire au meilleur. Sert au système ET à l'agrégat de la machine.
STATE_SEVERITY = (
    "stale", "security", "reboot", "blind", "updates", "ok", "unmanaged",
)

PACKAGE_MANAGERS = [
    ("apt", "apt"), ("dnf", "dnf"), ("pacman", "pacman"), ("zypper", "zypper"),
    ("apk", "apk"), ("bootc", "bootc / rpm-ostree"), ("winget", "winget"),
    ("other", "Other"),
]

PATCH_STATES = [
    ("unmanaged", "Not tracked"),
    ("stale", "Silent"),
    ("security", "Security updates pending"),
    ("reboot", "Reboot required"),
    ("blind", "Unknown count"),
    ("updates", "Updates pending"),
    ("ok", "Up to date"),
]


class BfPatchSystem(models.Model):
    _name = "bf.patch.system"
    _description = "System installed on a fleet endpoint"
    _order = "endpoint_id, name"
    _inherit = ["mail.thread"]

    name = fields.Char(
        string="System", required=True,
        help="Name of the installation, not of the machine: "
             "\"laptop-linux\" and \"laptop-windows\" are two systems "
             "on the same laptop.",
    )
    endpoint_id = fields.Many2one(
        comodel_name="hosting.endpoint", string="Endpoint",
        required=True, ondelete="cascade", index=True,
    )
    partner_id = fields.Many2one(
        related="endpoint_id.partner_id", string="Client", store=True, index=True
    )
    active = fields.Boolean(default=True)

    # ⚠️ Identité de l'INSTALLATION. Une réinstallation en tire un nouveau.
    machine_id = fields.Char(
        string="machine-id", index=True, copy=False,
        help="/etc/machine-id on Linux, MachineGuid on Windows. "
             "Identifies the installed system, never the physical machine.",
    )
    hostname = fields.Char(string="Hostname")
    os_family = fields.Selection(
        selection=[("linux", "Linux"), ("windows", "Windows"),
                   ("macos", "macOS"), ("other", "Other")],
        string="OS family", default="linux", required=True,
    )
    os_release = fields.Char(string="Reported system", readonly=True)

    # --- Agent ---
    patch_managed = fields.Boolean(string="Tracked", copy=False)
    agent_token_hash = fields.Char(
        string="Token hash", index=True, copy=False,
        groups="hosting_management.group_hosting_manager",
    )
    agent_last_report = fields.Datetime(
        string="Last report", copy=False, readonly=True, index=True
    )
    agent_last_poll = fields.Datetime(
        string="Last poll", readonly=True, copy=False,
        help="When the agent last asked whether it had a job. Separate "
             "from the report: the two timers do not run at the same pace.",
    )
    agent_version = fields.Char(string="Agent version", readonly=True)

    # --- Ce que le relevé rapporte ---
    kernel_running = fields.Char(string="Running kernel", readonly=True)
    kernel_installed = fields.Char(string="Installed kernel", readonly=True)
    boot_time = fields.Datetime(string="Booted on", readonly=True)

    reboot_required = fields.Boolean(string="Reboot required", readonly=True)
    reboot_pending_since = fields.Datetime(
        string="Reboot pending since", readonly=True
    )
    reboot_pending_days = fields.Integer(
        string="Days pending", compute="_compute_reboot_pending_days"
    )
    reboot_packages = fields.Char(string="Reboot required by", readonly=True)

    package_manager = fields.Selection(
        selection=PACKAGE_MANAGERS, string="Package manager",
        readonly=True,
    )
    pending_known = fields.Boolean(
        string="Reliable count", readonly=True, default=True,
        help="False when the package manager did not respond. An unknown "
             "count is NOT a zero count.",
    )
    pending_count = fields.Integer(string="Pending packages", readonly=True)
    pending_security_count = fields.Integer(string="Pending security packages", readonly=True)
    pending_delta = fields.Integer(
        string="Change since previous report", readonly=True
    )

    auto_update_mode = fields.Selection(
        selection=[("unknown", "Unknown"), ("off", "Off"),
                   ("download", "Download only"),
                   ("security", "Security only"), ("all", "All")],
        string="Automatic updates", readonly=True,
    )
    auto_update_detail = fields.Char(
        string="Auto-updater details", readonly=True
    )

    disk_root_pct = fields.Integer(string="/ used (%)", readonly=True)
    disk_boot_pct = fields.Integer(string="/boot used (%)", readonly=True)
    disk_tight = fields.Boolean(
        string="Low disk space", compute="_compute_disk_tight", store=True
    )

    os_support_end = fields.Date(string="End of support", readonly=True)
    os_support_state = fields.Selection(
        selection=[("supported", "Supported"),
                   ("ending_soon", "End of support soon"),
                   ("ended", "Out of support"),
                   ("rolling", "Rolling release"),
                   ("unknown", "Unknown")],
        string="Support status", readonly=True,
    )

    apply_allowed = fields.Boolean(
        string="Remote apply allowed", readonly=True,
        help="The file /etc/symbifox/apply-allowed exists on the machine. "
             "The machine itself declares it with each report: consent is "
             "LOCAL, set by hand, and withdrawn without going through "
             "Odoo.",
    )
    job_ids = fields.One2many(
        comodel_name="bf.patch.job", inverse_name="system_id",
        string="Update jobs",
    )
    job_pending_count = fields.Integer(
        string="Queued jobs", compute="_compute_job_pending_count",
    )
    # 🔴 Un ordre appliqué ne rajeunit PAS les compteurs. L'agent rapporte
    # l'issue de l'ordre, pas un relevé : « 50 paquets en attente » reste à
    # l'écran juste après une application réussie, jusqu'au relevé du
    # lendemain. Le module refuse de deviner le nouveau compte (ce serait
    # exactement le mensonge qu'il combat) ; il DIT donc que les compteurs
    # sont antérieurs.
    applied_after_report = fields.Datetime(
        string="Job applied since the report",
        compute="_compute_applied_after_report",
        help="Date of the last job applied after the last report. As long "
             "as it is set, the counters shown predate that application.",
    )

    report_ids = fields.One2many(
        comodel_name="bf.patch.report", inverse_name="system_id",
        string="Reports",
    )
    report_count = fields.Integer(
        string="Reports", compute="_compute_report_count"
    )

    patch_state = fields.Selection(
        selection=PATCH_STATES, string="Update status",
        compute="_compute_patch_state", store=True, index=True,
    )

    _sql_constraints = [
        ("machine_id_uniq", "unique(machine_id)",
         "This machine-id is already used by another system: it is the "
         "same installation, seen twice."),
    ]

    # ------------------------------------------------------------------
    def _compute_reboot_pending_days(self):
        now = fields.Datetime.now()
        for system in self:
            since = system.reboot_pending_since
            system.reboot_pending_days = (now - since).days if since else 0

    @api.depends("job_ids.state", "job_ids.finished_at", "agent_last_report")
    def _compute_applied_after_report(self):
        for system in self:
            applied = system.job_ids.filtered(
                lambda job: job.state == "done" and job.finished_at
                and (not system.agent_last_report
                     or job.finished_at > system.agent_last_report)
            )
            system.applied_after_report = max(
                applied.mapped("finished_at"), default=False
            )

    @api.depends("job_ids.state")
    def _compute_job_pending_count(self):
        for system in self:
            system.job_pending_count = len(system.job_ids.filtered(
                lambda job: job.state in ("queued", "claimed", "running")
            ))

    @api.depends("disk_root_pct", "disk_boot_pct")
    def _compute_disk_tight(self):
        for system in self:
            system.disk_tight = max(
                system.disk_root_pct or 0, system.disk_boot_pct or 0
            ) >= DISK_WARN_PCT

    def _compute_report_count(self):
        grouped = self.env["bf.patch.report"]._read_group(
            [("system_id", "in", self.ids)], ["system_id"], ["__count"]
        )
        counts = {system.id: total for system, total in grouped}
        for system in self:
            system.report_count = counts.get(system.id, 0)

    @api.depends("patch_managed", "agent_last_report", "pending_known",
                 "pending_security_count", "reboot_required", "pending_count")
    def _compute_patch_state(self):
        limit = fields.Datetime.subtract(
            fields.Datetime.now(), hours=STALE_AFTER_HOURS
        )
        for system in self:
            if not system.patch_managed:
                system.patch_state = "unmanaged"
            elif not system.agent_last_report or system.agent_last_report < limit:
                system.patch_state = "stale"
            elif system.pending_security_count:
                system.patch_state = "security"
            elif system.reboot_required:
                system.patch_state = "reboot"
            elif not system.pending_known:
                system.patch_state = "blind"
            elif system.pending_count:
                system.patch_state = "updates"
            else:
                system.patch_state = "ok"

    # ------------------------------------------------------------------
    @api.model
    def _hash_token(self, token):
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    @api.model
    def _new_token(self):
        return secrets.token_urlsafe(32)

    @api.model
    def _resolve_agent(self, token):
        if not token or len(token) < 20:
            return self.browse()
        return self.sudo().search(
            [("agent_token_hash", "=", self._hash_token(token))], limit=1
        )

    def action_view_jobs(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Jobs for %s", self.name),
            "res_model": "bf.patch.job",
            "view_mode": "list,form",
            "domain": [("system_id", "=", self.id)],
            "context": {"default_system_id": self.id},
        }

    def action_revoke_agent(self):
        self.ensure_one()
        self.endpoint_id._check_patch_manager()
        self.sudo().write({"agent_token_hash": False, "patch_managed": False})
        self.message_post(body=_("Update agent revoked."))
        return True

    # ------------------------------------------------------------------
    MIN_REPORT_INTERVAL_SECONDS = 60

    # Le minuteur d'interrogation bat à la minute. Le plancher doit rester SOUS
    # l'intervalle le plus court qu'une gigue puisse produire, sinon un agent
    # parfaitement sain se ferait refuser en 429 de temps en temps et on
    # chercherait longtemps pourquoi. Il n'est pas là pour rythmer un agent
    # sain, mais pour qu'un agent en dérive — ou un jeton volé — ne puisse pas
    # marteler la route.
    MIN_POLL_INTERVAL_SECONDS = 20

    def _poll_too_soon(self):
        self.ensure_one()
        last = self.agent_last_poll
        if not last:
            return False
        return (fields.Datetime.now() - last).total_seconds() \
            < self.MIN_POLL_INTERVAL_SECONDS

    def _touch_poll(self):
        self.ensure_one()
        # ⚠️ Écriture directe et non `write()` du modèle complet : marquer une
        # interrogation ne doit rien recalculer ni toucher `agent_last_report`,
        # qui porte la règle « un relevé absent est une alerte ». Une
        # interrogation n'est PAS un relevé : une machine qui interroge sans
        # jamais relever doit quand même basculer en muet.
        self.sudo().write({"agent_last_poll": fields.Datetime.now()})

    def _report_too_soon(self):
        self.ensure_one()
        last = self.agent_last_report
        if not last:
            return False
        return (fields.Datetime.now() - last).total_seconds() \
            < self.MIN_REPORT_INTERVAL_SECONDS

    def _apply_report(self, data, payload=None):
        """Créer le relevé et reporter son état sur la fiche du système."""
        self.ensure_one()
        report_model = self.env["bf.patch.report"].sudo()
        previous = report_model.search(
            [("system_id", "=", self.id)],
            order="report_date desc, id desc", limit=1,
        )

        values = {key: data.get(key) for key in report_model._fields
                  if key in data}
        values.update({
            "system_id": self.id,
            "report_date": fields.Datetime.now(),
            "payload": payload,
        })
        pending = data.get("pending_count") or 0
        values["pending_delta"] = (pending - (previous.pending_count or 0)) \
            if previous else 0

        report = report_model.create(values)

        packages = data.get("packages") or []
        if packages:
            self.env["bf.patch.package"].sudo().create([
                dict(pkg, report_id=report.id) for pkg in packages
            ])

        from .bf_patch_report import MIRRORED_FIELDS
        mirrored = {name: getattr(report, name) for name in MIRRORED_FIELDS
                    if name in self._fields}
        mirrored.update({
            "agent_last_report": report.report_date,
            "pending_delta": report.pending_delta,
        })
        self.sudo().write(mirrored)
        return report

    # ------------------------------------------------------------------
    @api.model
    def _bf_reader_lang(self):
        """La langue des notes que le module écrit sans personne derrière.

        Un enrôlement par l'agent, un travail planifié : personne n'agit, et
        les lecteurs sont l'équipe d'hébergement de la société. Sa langue si
        elle est installée, sinon l'anglais, la langue de la source (avant, ces
        notes prenaient celle d'OdooBot ou de la requête publique de l'agent).
        """
        installees = {code for code, _nom in self.env["res.lang"].get_installed()}
        lang = self.env.company.partner_id.lang
        return lang if lang in installees else "en_US"

    @api.model
    def _cron_refresh_patch_state(self):
        """Rejouer `patch_state` pour que « muet » finisse par arriver."""
        managed = self.sudo().search([("patch_managed", "=", True)])
        before = {system.id: system.patch_state for system in managed}
        managed.modified(["agent_last_report"])
        managed.flush_recordset(["patch_state"])
        gone_quiet = managed.filtered(
            lambda s: s.patch_state == "stale" and before.get(s.id) != "stale"
        )
        lu = self.with_context(lang=self._bf_reader_lang())
        for system in gone_quiet:
            system.message_post(
                body=lu.env._("No report for more than %s hours: this system has "
                              "gone silent.", STALE_AFTER_HOURS)
            )
        if gone_quiet:
            _logger.warning(
                "bf_hosting_patch : %d système(s) devenu(s) muet(s) : %s",
                len(gone_quiet), ", ".join(gone_quiet.mapped("name")),
            )
        return len(gone_quiet)
