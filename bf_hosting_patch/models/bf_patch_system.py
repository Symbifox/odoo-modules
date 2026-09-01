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
    ("other", "Autre"),
]

PATCH_STATES = [
    ("unmanaged", "Non suivi"),
    ("stale", "Muet"),
    ("security", "Sécurité en attente"),
    ("reboot", "Redémarrage requis"),
    ("blind", "Compte inconnu"),
    ("updates", "Mises à jour en attente"),
    ("ok", "À jour"),
]


class BfPatchSystem(models.Model):
    _name = "bf.patch.system"
    _description = "Système installé sur un poste du parc"
    _order = "endpoint_id, name"
    _inherit = ["mail.thread"]

    name = fields.Char(
        string="Système", required=True,
        help="Nom de l'installation, pas de la machine : « poste-linux » "
             "et « poste-windows » sont deux systèmes du même portable.",
    )
    endpoint_id = fields.Many2one(
        comodel_name="hosting.endpoint", string="Poste",
        required=True, ondelete="cascade", index=True,
    )
    partner_id = fields.Many2one(
        related="endpoint_id.partner_id", string="Client", store=True, index=True
    )
    active = fields.Boolean(default=True)

    # ⚠️ Identité de l'INSTALLATION. Une réinstallation en tire un nouveau.
    machine_id = fields.Char(
        string="machine-id", index=True, copy=False,
        help="/etc/machine-id sous Linux, MachineGuid sous Windows. Identifie "
             "le système installé, jamais la machine physique.",
    )
    hostname = fields.Char(string="Nom d'hôte")
    os_family = fields.Selection(
        selection=[("linux", "Linux"), ("windows", "Windows"),
                   ("macos", "macOS"), ("other", "Autre")],
        string="Famille", default="linux", required=True,
    )
    os_release = fields.Char(string="Système relevé", readonly=True)

    # --- Agent ---
    patch_managed = fields.Boolean(string="Suivi", copy=False)
    agent_token_hash = fields.Char(
        string="Empreinte du jeton", index=True, copy=False,
        groups="hosting_management.group_hosting_manager",
    )
    agent_last_report = fields.Datetime(
        string="Dernier relevé", copy=False, readonly=True, index=True
    )
    agent_last_poll = fields.Datetime(
        string="Dernière interrogation", readonly=True, copy=False,
        help="Quand l'agent a demandé pour la dernière fois s'il avait un "
             "ordre. Distinct du relevé : les deux minuteurs n'ont pas la "
             "même cadence.",
    )
    agent_version = fields.Char(string="Version de l'agent", readonly=True)

    # --- Ce que le relevé rapporte ---
    kernel_running = fields.Char(string="Noyau chargé", readonly=True)
    kernel_installed = fields.Char(string="Noyau installé", readonly=True)
    boot_time = fields.Datetime(string="Démarré le", readonly=True)

    reboot_required = fields.Boolean(string="Redémarrage requis", readonly=True)
    reboot_pending_since = fields.Datetime(
        string="Redémarrage en attente depuis", readonly=True
    )
    reboot_pending_days = fields.Integer(
        string="Jours d'attente", compute="_compute_reboot_pending_days"
    )
    reboot_packages = fields.Char(string="Redémarrage dû à", readonly=True)

    package_manager = fields.Selection(
        selection=PACKAGE_MANAGERS, string="Gestionnaire de paquets",
        readonly=True,
    )
    pending_known = fields.Boolean(
        string="Compte fiable", readonly=True, default=True,
        help="Faux quand le gestionnaire de paquets n'a pas répondu. Un compte "
             "inconnu n'est PAS un compte à zéro.",
    )
    pending_count = fields.Integer(string="Paquets en attente", readonly=True)
    pending_security_count = fields.Integer(string="Dont sécurité", readonly=True)
    pending_delta = fields.Integer(
        string="Écart au relevé précédent", readonly=True
    )

    auto_update_mode = fields.Selection(
        selection=[("unknown", "Inconnu"), ("off", "Aucun"),
                   ("download", "Téléchargement seul"),
                   ("security", "Sécurité seulement"), ("all", "Tout")],
        string="Mise à jour automatique", readonly=True,
    )
    auto_update_detail = fields.Char(
        string="Détail de l'auto-updateur", readonly=True
    )

    disk_root_pct = fields.Integer(string="/ occupé (%)", readonly=True)
    disk_boot_pct = fields.Integer(string="/boot occupé (%)", readonly=True)
    disk_tight = fields.Boolean(
        string="Disque serré", compute="_compute_disk_tight", store=True
    )

    os_support_end = fields.Date(string="Fin de support", readonly=True)
    os_support_state = fields.Selection(
        selection=[("supported", "Supporté"),
                   ("ending_soon", "Fin de support proche"),
                   ("ended", "Hors support"),
                   ("rolling", "Roulement continu"),
                   ("unknown", "Inconnu")],
        string="État du support", readonly=True,
    )

    apply_allowed = fields.Boolean(
        string="Application autorisée", readonly=True,
        help="Le fichier /etc/symbifox/apply-allowed existe sur la machine. "
             "C'est la machine qui le déclare à chaque relevé : le consentement "
             "est LOCAL, posé à la main, et se retire sans passer par Odoo.",
    )
    job_ids = fields.One2many(
        comodel_name="bf.patch.job", inverse_name="system_id",
        string="Ordres de mise à jour",
    )
    job_pending_count = fields.Integer(
        string="Ordres en file", compute="_compute_job_pending_count",
    )

    report_ids = fields.One2many(
        comodel_name="bf.patch.report", inverse_name="system_id",
        string="Relevés",
    )
    report_count = fields.Integer(
        string="Relevés", compute="_compute_report_count"
    )

    patch_state = fields.Selection(
        selection=PATCH_STATES, string="État des mises à jour",
        compute="_compute_patch_state", store=True, index=True,
    )

    _sql_constraints = [
        ("machine_id_uniq", "unique(machine_id)",
         "Ce machine-id est déjà porté par un autre système : c'est la même "
         "installation, vue deux fois."),
    ]

    # ------------------------------------------------------------------
    def _compute_reboot_pending_days(self):
        now = fields.Datetime.now()
        for system in self:
            since = system.reboot_pending_since
            system.reboot_pending_days = (now - since).days if since else 0

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
            "name": f"Ordres de {self.name}",
            "res_model": "bf.patch.job",
            "view_mode": "list,form",
            "domain": [("system_id", "=", self.id)],
            "context": {"default_system_id": self.id},
        }

    def action_revoke_agent(self):
        self.ensure_one()
        self.endpoint_id._check_patch_manager()
        self.sudo().write({"agent_token_hash": False, "patch_managed": False})
        self.message_post(body=_("Agent de mise à jour révoqué."))
        return True

    # ------------------------------------------------------------------
    MIN_REPORT_INTERVAL_SECONDS = 60

    # Le minuteur d'interrogation bat aux 15 minutes. Le plancher est bien plus
    # bas que la cadence : il n'est pas là pour rythmer un agent sain, mais pour
    # qu'un agent en dérive — ou un jeton volé — ne puisse pas marteler la route.
    MIN_POLL_INTERVAL_SECONDS = 30

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
    def _cron_refresh_patch_state(self):
        """Rejouer `patch_state` pour que « muet » finisse par arriver."""
        managed = self.sudo().search([("patch_managed", "=", True)])
        before = {system.id: system.patch_state for system in managed}
        managed.modified(["agent_last_report"])
        managed.flush_recordset(["patch_state"])
        gone_quiet = managed.filtered(
            lambda s: s.patch_state == "stale" and before.get(s.id) != "stale"
        )
        for system in gone_quiet:
            system.message_post(
                body=_("Aucun relevé depuis plus de %s heures : ce système ne "
                       "parle plus.", STALE_AFTER_HOURS)
            )
        if gone_quiet:
            _logger.warning(
                "bf_hosting_patch : %d système(s) devenu(s) muet(s) : %s",
                len(gone_quiet), ", ".join(gone_quiet.mapped("name")),
            )
        return len(gone_quiet)
