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
    _description = "Endpoint update report"
    _order = "report_date desc, id desc"
    _rec_name = "display_label"

    system_id = fields.Many2one(
        comodel_name="bf.patch.system",
        string="System",
        required=True,
        ondelete="cascade",
        index=True,
    )
    # Le relevé appartient au SYSTÈME ; la machine se déduit. Garder les deux
    # évite de traverser deux relations dans chaque vue et chaque règle.
    endpoint_id = fields.Many2one(
        related="system_id.endpoint_id", string="Endpoint", store=True, index=True
    )
    partner_id = fields.Many2one(
        related="system_id.partner_id", string="Client", store=True, index=True
    )
    report_date = fields.Datetime(
        string="Report date", required=True, index=True
    )
    display_label = fields.Char(
        string="Report", compute="_compute_display_label"
    )

    agent_version = fields.Char(string="Agent version")
    os_release = fields.Char(string="System")
    kernel_running = fields.Char(string="Running kernel")
    kernel_installed = fields.Char(string="Installed kernel")
    boot_time = fields.Datetime(string="Boot time")

    apply_allowed = fields.Boolean(
        string="Remote apply allowed",
        help="The file /etc/symbifox/apply-allowed existed on the machine "
             "at report time. Kept in history here so we can tell WHEN "
             "consent was given or withdrawn.",
    )
    reboot_required = fields.Boolean(string="Reboot required")
    reboot_pending_since = fields.Datetime(string="Pending since")
    reboot_packages = fields.Char(string="Reboot required by")

    package_manager = fields.Selection(
        selection=[
            ("apt", "apt"),
            ("dnf", "dnf"),
            ("pacman", "pacman"),
            ("zypper", "zypper"),
            ("apk", "apk"),
            ("bootc", "bootc / rpm-ostree"),
            ("other", "Other"),
        ],
        string="Package manager",
    )
    pending_known = fields.Boolean(
        string="Reliable count",
        default=True,
        help="False when the package manager did not respond. An unknown "
             "count is NOT a zero count.",
    )
    pending_count = fields.Integer(string="Pending packages")
    pending_security_count = fields.Integer(string="Pending security packages")
    pending_delta = fields.Integer(
        string="Change since previous report",
        help="Positive: the backlog has grown since the previous report.",
    )

    # On relève le MODE, jamais la présence : trois machines du parc BF ont un
    # auto-updateur configuré et aucune des trois n'applique ce qui attend.
    auto_update_mode = fields.Selection(
        selection=[
            ("unknown", "Unknown"),
            ("off", "Off"),
            ("download", "Download only"),
            ("security", "Security only"),
            ("all", "All"),
        ],
        string="Automatic updates",
    )
    auto_update_detail = fields.Char(
        string="Auto-updater details",
        help="Allowed origins, timer name, what the log says about it.",
    )

    disk_root_pct = fields.Integer(string="/ used (%)")
    disk_boot_pct = fields.Integer(string="/boot used (%)")

    os_support_end = fields.Date(
        string="End of support",
        help="Read on the machine: SUPPORT_END from os-release, or "
             "distro-info.",
    )
    os_support_state = fields.Selection(
        selection=[
            ("supported", "Supported"),
            ("ending_soon", "End of support soon"),
            ("ended", "Out of support"),
            ("rolling", "Rolling release"),
            ("unknown", "Unknown"),
        ],
        string="Support status",
    )

    payload = fields.Text(
        string="Raw payload",
        help="What the agent sent, to replay a case without asking the "
             "machine again.",
    )
    package_ids = fields.One2many(
        comodel_name="bf.patch.package",
        inverse_name="report_id",
        string="Pending packages",
    )

    @api.depends("system_id.name", "report_date")
    def _compute_display_label(self):
        for report in self:
            name = report.system_id.name or _("Unknown system")
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
