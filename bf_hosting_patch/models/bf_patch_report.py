# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""bf.patch.report — un relevé déposé par l'agent d'une machine.

Le relevé MONTE : Odoo n'a aucun chemin vers les machines, et n'en veut pas.
Chaque passe de `symbifox-hostd` crée un enregistrement, qui reporte ensuite
son état sur la fiche `hosting.endpoint`.
"""

import logging

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)

# Les compteurs bruts que le relevé recopie tels quels sur la fiche du poste.
# Tenu ici pour qu'ajouter un champ au relevé n'oblige pas à retoucher deux
# endroits ni à se souvenir lequel.
MIRRORED_FIELDS = (
    "agent_version",
    # 🔴 Sans cette ligne, le consentement local n'atteignait JAMAIS la fiche du
    # système : le contrôleur le recevait, `bf.patch.report` ne le portait pas,
    # et la recopie ne le nommait pas. Le serveur refusait donc tout ordre à
    # toute machine, pour toujours — lot 3 était inopérant de bout en bout.
    # Le commentaire en tête de cette liste dit exactement pourquoi elle existe.
    "apply_allowed",
    "pending_known",
    "os_release",
    "kernel_running",
    "kernel_installed",
    "reboot_required",
    "reboot_pending_since",
    "reboot_packages",
    "package_manager",
    "pending_count",
    "pending_security_count",
    "auto_update_mode",
    "auto_update_detail",
    "disk_root_pct",
    "disk_boot_pct",
    "boot_time",
    "os_support_end",
    "os_support_state",
)


class BfPatchReport(models.Model):
    _name = "bf.patch.report"
    _description = "Relevé de mise à jour d'un poste"
    _order = "report_date desc, id desc"
    _rec_name = "display_label"

    system_id = fields.Many2one(
        comodel_name="bf.patch.system",
        string="Système",
        required=True,
        ondelete="cascade",
        index=True,
    )
    # Le relevé appartient au SYSTÈME ; la machine se déduit. Garder les deux
    # évite de traverser deux relations dans chaque vue et chaque règle.
    endpoint_id = fields.Many2one(
        related="system_id.endpoint_id", string="Poste", store=True, index=True
    )
    partner_id = fields.Many2one(
        related="system_id.partner_id", string="Client", store=True, index=True
    )
    report_date = fields.Datetime(
        string="Date du relevé", required=True, index=True
    )
    display_label = fields.Char(
        string="Relevé", compute="_compute_display_label"
    )

    agent_version = fields.Char(string="Version de l'agent")
    os_release = fields.Char(string="Système")
    kernel_running = fields.Char(string="Noyau chargé")
    kernel_installed = fields.Char(string="Noyau installé")
    boot_time = fields.Datetime(string="Démarrée le")

    apply_allowed = fields.Boolean(
        string="Application autorisée",
        help="Le fichier /etc/symbifox/apply-allowed existait sur la machine au "
             "moment du relevé. Historisé ici pour qu'on puisse dire QUAND le "
             "consentement a été donné ou retiré.",
    )
    reboot_required = fields.Boolean(string="Redémarrage requis")
    reboot_pending_since = fields.Datetime(string="En attente depuis")
    reboot_packages = fields.Char(string="Redémarrage dû à")

    package_manager = fields.Selection(
        selection=[
            ("apt", "apt"),
            ("dnf", "dnf"),
            ("pacman", "pacman"),
            ("zypper", "zypper"),
            ("apk", "apk"),
            ("bootc", "bootc / rpm-ostree"),
            ("other", "Autre"),
        ],
        string="Gestionnaire de paquets",
    )
    pending_known = fields.Boolean(
        string="Compte fiable",
        default=True,
        help="Faux quand le gestionnaire de paquets n'a pas répondu. Un compte "
             "inconnu n'est PAS un compte à zéro.",
    )
    pending_count = fields.Integer(string="Paquets en attente")
    pending_security_count = fields.Integer(string="Dont sécurité")
    pending_delta = fields.Integer(
        string="Écart au relevé précédent",
        help="Positif : le retard a grandi depuis le relevé d'avant.",
    )

    # On relève le MODE, jamais la présence : trois machines du parc BF ont un
    # auto-updateur configuré et aucune des trois n'applique ce qui attend.
    auto_update_mode = fields.Selection(
        selection=[
            ("unknown", "Inconnu"),
            ("off", "Aucun"),
            ("download", "Téléchargement seul"),
            ("security", "Sécurité seulement"),
            ("all", "Tout"),
        ],
        string="Mise à jour automatique",
    )
    auto_update_detail = fields.Char(
        string="Détail de l'auto-updateur",
        help="Origines autorisées, nom du minuteur, ce que le journal en dit.",
    )

    disk_root_pct = fields.Integer(string="/ occupé (%)")
    disk_boot_pct = fields.Integer(string="/boot occupé (%)")

    os_support_end = fields.Date(
        string="Fin de support",
        help="Lue sur la machine : SUPPORT_END d'os-release, ou distro-info.",
    )
    os_support_state = fields.Selection(
        selection=[
            ("supported", "Supporté"),
            ("ending_soon", "Fin de support proche"),
            ("ended", "Hors support"),
            ("rolling", "Roulement continu"),
            ("unknown", "Inconnu"),
        ],
        string="État du support",
    )

    payload = fields.Text(
        string="Charge utile brute",
        help="Ce que l'agent a envoyé, pour rejouer un cas sans redemander à "
             "la machine.",
    )
    package_ids = fields.One2many(
        comodel_name="bf.patch.package",
        inverse_name="report_id",
        string="Paquets en attente",
    )

    @api.depends("system_id.name", "report_date")
    def _compute_display_label(self):
        for report in self:
            name = report.system_id.name or _("Système inconnu")
            when = fields.Datetime.to_string(report.report_date) or ""
            report.display_label = f"{name} — {when}"

    @api.model
    def _cron_purge(self, days=90):
        """Purger les vieux relevés. C'est de la mesure, pas de l'archive."""
        limit = fields.Datetime.subtract(fields.Datetime.now(), days=days)
        stale = self.search([("report_date", "<", limit)])
        count = len(stale)
        stale.unlink()
        if count:
            _logger.info("bf.patch.report : %d relevés purgés (> %d jours)",
                         count, days)
        return count
