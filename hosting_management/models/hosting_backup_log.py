# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import logging
from datetime import datetime, timedelta

import pytz

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


def _icp_truthy(value, default=True):
    """Interpréter un paramètre `ir.config_parameter` booléen de façon tolérante.

    Les champs `Boolean` liés via `config_parameter` sont stockés par Odoo sous
    la forme `"True"` / `"False"`, alors que les seeds/`set_param` manuels
    utilisent souvent `"1"` / `"0"`. On accepte les deux pour éviter qu'un simple
    enregistrement du formulaire de configuration ne désactive silencieusement
    l'envoi du rapport (cf. incident BKP-00168, 2026-06-30).
    """
    if value is None or value == "":
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on", "t")


class HostingBackupRun(models.Model):
    """Représente une exécution de sauvegarde (exécution quotidienne de tous les scripts de sauvegarde)."""

    _name = "hosting.backup.run"
    _description = "Exécution de sauvegarde"
    _order = "run_date desc, id desc"
    _inherit = ["mail.thread", "mail.activity.mixin"]

    name = fields.Char(
        string="Référence",
        readonly=True,
        copy=False,
        default="New",
    )
    run_date = fields.Datetime(
        string="Date d'exécution",
        required=True,
        default=fields.Datetime.now,
        tracking=True,
    )
    hostname = fields.Char(
        string="Nom d'hôte du serveur",
        tracking=True,
    )
    backup_root = fields.Char(
        string="Emplacement de sauvegarde",
    )
    report_type = fields.Selection(
        selection=[
            ("legacy", "Legacy ZIP"),
            ("restic", "Restic"),
        ],
        string="Type de rapport",
        default="legacy",
        required=True,
        index=True,
        tracking=True,
    )
    repository_ids = fields.Many2many(
        comodel_name="hosting.backup.repository",
        compute="_compute_repository_ids",
        string="Dépôts touchés",
    )
    state = fields.Selection(
        selection=[
            ("success", "Tous réussis"),
            ("warning", "Réussi avec avertissements"),
            ("partial", "Succès partiel"),
            ("failed", "Échoué"),
        ],
        string="État",
        compute="_compute_state",
        store=True,
        tracking=True,
    )
    total_count = fields.Integer(
        string="Total des services",
        compute="_compute_counts",
        store=True,
    )
    success_count = fields.Integer(
        string="Réussis",
        compute="_compute_counts",
        store=True,
    )
    warning_count = fields.Integer(
        string="Avertissements",
        compute="_compute_counts",
        store=True,
        help="Services dont la cible principale a produit un snapshot mais "
             "où une sous-cible secondaire a été ignorée (data captée).",
    )
    failed_count = fields.Integer(
        string="Échoués",
        compute="_compute_counts",
        store=True,
    )
    skipped_count = fields.Integer(
        string="Ignorés",
        compute="_compute_counts",
        store=True,
    )
    line_ids = fields.One2many(
        comodel_name="hosting.backup.line",
        inverse_name="run_id",
        string="Détails de sauvegarde",
    )
    notes = fields.Text(
        string="Notes",
    )
    report_sent = fields.Boolean(
        string="Rapport envoyé",
        default=False,
    )
    report_sent_date = fields.Datetime(
        string="Date d'envoi du rapport",
    )
    ntfy_alert_sent = fields.Boolean(
        string="Alerte push envoyée",
        default=False,
        copy=False,
    )
    company_id = fields.Many2one(
        comodel_name="res.company",
        string="Société",
        default=lambda self: self.env.company,
    )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", "New") == "New":
                vals["name"] = self.env["ir.sequence"].next_by_code(
                    "hosting.backup.run"
                ) or "New"
        return super().create(vals_list)

    @api.depends("line_ids", "line_ids.status", "line_ids.snapshot_count")
    def _compute_counts(self):
        for run in self:
            run.total_count = len(run.line_ids)
            # Une ligne 'partial' avec ≥1 snapshot = data captée → compte comme succès (avec avertissement).
            # Une ligne 'partial' sans snapshot = vraie perte → compte comme échec.
            run.success_count = len(
                run.line_ids.filtered(
                    lambda l: l.status == "success"
                    or (l.status == "partial" and l.snapshot_count > 0)
                )
            )
            run.warning_count = len(
                run.line_ids.filtered(
                    lambda l: l.status == "partial" and l.snapshot_count > 0
                )
            )
            run.failed_count = len(
                run.line_ids.filtered(
                    lambda l: l.status == "failed"
                    or (l.status == "partial" and l.snapshot_count == 0)
                )
            )
            run.skipped_count = len(
                run.line_ids.filtered(lambda l: l.status == "skipped")
            )

    @api.depends("line_ids", "line_ids.status", "line_ids.all_verified", "line_ids.snapshot_count", "report_type")
    def _compute_state(self):
        for run in self:
            if not run.line_ids:
                run.state = False
            elif run.failed_count > 0:
                run.state = "failed" if run.success_count == 0 else "partial"
            elif run.report_type == "restic":
                # Restic : si toutes les lignes ont produit ≥1 snapshot, la sauvegarde est
                # complète. Les lignes 'partial' (sous-cible secondaire ignorée mais data
                # captée) déclenchent l'état 'warning' plutôt que 'partial'.
                if run.warning_count > 0:
                    run.state = "warning"
                else:
                    run.state = "success"
            else:
                # Legacy : vérifier uniquement si toutes les sauvegardes réussies sont vérifiées
                has_unverified = any(
                    not line.all_verified
                    for line in run.line_ids
                    if line.status == "success" and line.file_ids
                )
                run.state = "partial" if has_unverified else "success"

    @api.depends("line_ids.snapshot_ids.repository_id")
    def _compute_repository_ids(self):
        for run in self:
            run.repository_ids = run.line_ids.mapped(
                "snapshot_ids.repository_id"
            )

    def _maybe_send_ntfy_alert(self):
        """Appeler après finalisation de l'exécution (lignes créées, état calculé)."""
        for run in self:
            if run.state in ("failed", "partial") and not run.ntfy_alert_sent:
                run._send_backup_ntfy_alert()
                run.ntfy_alert_sent = True

    def _send_backup_ntfy_alert(self):
        """Push ntfy lorsqu'une exécution se termine en échec ou en succès partiel."""
        self.ensure_one()
        Ntfy = self.env["hosting.ntfy"]
        failed_lines = self.line_ids.filtered(lambda l: l.status == "failed")
        unverified_lines = self.line_ids.filtered(
            lambda l: l.status == "success" and l.file_ids and not l.all_verified
        )
        if self.state == "failed":
            title = f"SAUVEGARDE ÉCHEC : {self.name or self.hostname or 'exécution'}"
            priority = "urgent"
            tags = "floppy_disk,rotating_light"
        else:
            title = f"SAUVEGARDE PARTIELLE : {self.name or self.hostname or 'exécution'}"
            priority = "high"
            tags = "floppy_disk,warning"
        body_lines = [
            f"Hôte : {self.hostname or 'N/D'}",
            (
                f"Succès : {self.success_count} / Avert. : {self.warning_count} / "
                f"Échecs : {self.failed_count} / Ignorés : {self.skipped_count}"
            ),
        ]
        if failed_lines:
            body_lines.append("")
            body_lines.append("Services en échec :")
            for line in failed_lines[:10]:
                err = (line.error_message or "").strip().splitlines()[0] if line.error_message else ""
                body_lines.append(
                    f"- {line.service_name}" + (f" : {err[:80]}" if err else "")
                )
            if len(failed_lines) > 10:
                body_lines.append(f"… et {len(failed_lines) - 10} de plus")
        if unverified_lines and not failed_lines:
            body_lines.append("")
            body_lines.append("Archives non vérifiées :")
            for line in unverified_lines[:10]:
                body_lines.append(f"- {line.service_name} ({line.backup_ratio})")
        Ntfy.send(
            title=title,
            body="\n".join(body_lines),
            priority=priority,
            tags=tags,
            click=Ntfy.record_url(self),
        )

    def action_send_report(self):
        """Envoyer le rapport de sauvegarde par courriel.

        Lit la configuration `hosting.backup_report_*` :
        - `enabled`        : interrupteur global
        - `only_on_issues` : ne déclenche le courriel que si l'état n'est pas success
        - `recipients`     : liste d'adresses séparées par des virgules (override
          du `email_to` du template)
        """
        self.ensure_one()
        ICP = self.env["ir.config_parameter"].sudo()
        if not _icp_truthy(ICP.get_param("hosting.backup_report_enabled", "1")):
            self._maybe_send_ntfy_alert()
            return False
        only_on_issues = _icp_truthy(
            ICP.get_param("hosting.backup_report_only_on_issues", "0"), default=False
        )
        if only_on_issues and self.state == "success":
            # Marquer comme « traité » pour éviter une nouvelle évaluation par le
            # cron tant que la fenêtre de 36 h n'est pas écoulée.
            self.write(
                {"report_sent": True, "report_sent_date": fields.Datetime.now()}
            )
            self.message_post(
                body="Courriel non envoyé (configuration : seulement en cas de problème).",
                subtype_xmlid="mail.mt_note",
            )
            self._maybe_send_ntfy_alert()
            return False
        template = self.env.ref(
            "hosting_management.email_template_backup_report", raise_if_not_found=False
        )
        if template:
            recipients = (ICP.get_param("hosting.backup_report_recipients") or "").strip()
            email_values = {"email_to": recipients} if recipients else None
            template.send_mail(self.id, force_send=True, email_values=email_values)
            self.write(
                {"report_sent": True, "report_sent_date": fields.Datetime.now()}
            )
            self.message_post(
                body=(
                    f"Rapport de sauvegarde envoyé à "
                    f"<strong>{recipients or template.email_to or '?'}</strong>."
                ),
                subtype_xmlid="mail.mt_note",
            )
        self._maybe_send_ntfy_alert()
        return True

    @api.model
    def _cron_send_daily_report(self):
        """Cron horaire : envoie le dernier rapport non-envoyé par couple
        (société, hôte) lorsque l'heure locale courante correspond à l'heure
        configurée. Le cron est inerte si :
        - les rapports sont désactivés
        - le mode d'envoi n'est pas « scheduled »
        - l'heure courante (TZ configurée) ne correspond pas
        """
        ICP = self.env["ir.config_parameter"].sudo()
        if not _icp_truthy(ICP.get_param("hosting.backup_report_enabled", "1")):
            return
        if ICP.get_param("hosting.backup_report_mode", "scheduled") != "scheduled":
            return
        try:
            target_hour = int(ICP.get_param("hosting.backup_report_send_hour", "6"))
        except (TypeError, ValueError):
            target_hour = 6
        tz_name = ICP.get_param("hosting.backup_report_timezone", "America/Toronto")
        try:
            tz = pytz.timezone(tz_name)
        except pytz.UnknownTimeZoneError:
            tz = pytz.timezone("America/Toronto")
        now_local = datetime.now(tz)
        # Fenêtre de rattrapage : on tente à l'heure cible ET à chaque passage
        # horaire suivant de la journée, jusqu'à ce que `report_sent` bascule.
        # Un échec d'envoi transitoire (SMTP, fenêtre manquée) est ainsi réessayé
        # au prochain tour plutôt que perdu jusqu'au lendemain (cf. BKP-00168).
        if now_local.hour < target_hour:
            return

        # Couvre 36h pour absorber tout décalage TZ + un éventuel report tardif.
        cutoff = fields.Datetime.now() - timedelta(hours=36)
        runs = self.search(
            [("run_date", ">=", cutoff), ("report_sent", "=", False)],
            order="run_date desc",
        )
        seen = set()
        sent = 0
        for run in runs:
            key = (run.company_id.id, run.hostname or "")
            if key in seen:
                continue
            seen.add(key)
            run_label = run.name or run.hostname or str(run.id)
            run_host = run.hostname or "N/D"
            try:
                if run.action_send_report():
                    sent += 1
            except Exception as exc:
                self.env.cr.rollback()
                _logger.exception(
                    "Échec de l'envoi du rapport de sauvegarde %s", run_label
                )
                # Le rattrapage horaire réessaiera, mais on alerte tout de suite
                # pour qu'un échec persistant ne reste pas silencieux.
                try:
                    self.env["hosting.ntfy"].send(
                        title=f"RAPPORT SAUVEGARDE : échec d'envoi ({run_label})",
                        body=(
                            f"Hôte : {run_host}\n"
                            f"L'envoi du courriel de rapport a échoué : "
                            f"{str(exc)[:200]}\n"
                            f"Nouvel essai au prochain passage horaire."
                        ),
                        priority="high",
                        tags="floppy_disk,email,warning",
                    )
                except Exception:
                    _logger.exception(
                        "Échec de l'alerte ntfy pour le rapport de sauvegarde %s",
                        run_label,
                    )
        if sent:
            _logger.info(
                "Rapport de sauvegarde quotidien : %d courriel(s) envoyé(s).", sent
            )


class HostingBackupLine(models.Model):
    """Représente une sauvegarde individuelle de service dans une exécution de sauvegarde."""

    _name = "hosting.backup.line"
    _description = "Ligne de sauvegarde"
    _order = "run_id desc, service_name"

    run_id = fields.Many2one(
        comodel_name="hosting.backup.run",
        string="Exécution de sauvegarde",
        required=True,
        ondelete="cascade",
    )
    service_name = fields.Char(
        string="Service",
        required=True,
    )
    status = fields.Selection(
        selection=[
            ("success", "Réussi"),
            ("partial", "Partiel"),
            ("failed", "Échoué"),
            ("skipped", "Ignoré"),
        ],
        string="État",
        required=True,
    )
    duration = fields.Char(
        string="Durée",
    )
    error_message = fields.Text(
        string="Message d'erreur",
    )
    container_count = fields.Integer(
        string="Conteneurs attendus",
        help="Nombre de conteneurs Docker attendus pour la sauvegarde",
        default=0,
    )
    verified_file_count = fields.Integer(
        string="Archives vérifiées",
        help="Nombre d'archives de sauvegarde vérifiées créées",
        default=0,
    )
    file_ids = fields.One2many(
        comodel_name="hosting.backup.file",
        inverse_name="line_id",
        string="Fichiers de sauvegarde",
    )
    snapshot_ids = fields.One2many(
        comodel_name="hosting.backup.snapshot",
        inverse_name="run_line_id",
        string="Snapshots Restic",
    )
    snapshot_count = fields.Integer(
        string="Snapshots",
        compute="_compute_snapshot_count",
    )
    report_type = fields.Selection(
        related="run_id.report_type",
        store=True,
        index=True,
    )
    exit_code = fields.Integer(
        string="Code de sortie",
        default=0,
        help="Code de sortie du script enfant (Restic). 0 = OK, autre = partial/failed",
    )

    # Champs calculés
    file_count = fields.Integer(
        string="Fichiers",
        compute="_compute_file_count",
    )
    backup_ratio = fields.Char(
        string="État de sauvegarde",
        compute="_compute_backup_ratio",
        help="Ratio d'archives vérifiées par rapport aux conteneurs attendus",
    )
    total_size = fields.Char(
        string="Taille totale",
        compute="_compute_total_size",
    )
    all_verified = fields.Boolean(
        string="Tous vérifiés",
        compute="_compute_all_verified",
    )

    @api.depends("file_ids", "snapshot_ids", "report_type")
    def _compute_file_count(self):
        for line in self:
            if line.report_type == "restic":
                line.file_count = len(line.snapshot_ids)
            else:
                line.file_count = len(line.file_ids)

    @api.depends("snapshot_ids")
    def _compute_snapshot_count(self):
        for line in self:
            line.snapshot_count = len(line.snapshot_ids)

    @api.depends("file_ids.size", "snapshot_ids.data_added_bytes", "report_type")
    def _compute_total_size(self):
        for line in self:
            if line.report_type == "restic":
                total = sum(line.snapshot_ids.mapped("data_added_bytes"))
                line.total_size = _human_bytes(total) if total else "-"
            else:
                # Legacy: concatenate string sizes like "1.2G", "456M"
                sizes = line.file_ids.mapped("size")
                line.total_size = ", ".join(filter(None, sizes)) or "-"

    @api.depends("file_ids.verified", "snapshot_ids", "status", "report_type")
    def _compute_all_verified(self):
        for line in self:
            if line.report_type == "restic":
                # Restic snapshots are inherently verified by the upload pipeline.
                line.all_verified = (
                    line.status == "success" and len(line.snapshot_ids) > 0
                )
            elif line.file_ids:
                line.all_verified = all(f.verified for f in line.file_ids)
            else:
                line.all_verified = False

    @api.depends("container_count", "verified_file_count", "snapshot_ids", "report_type")
    def _compute_backup_ratio(self):
        for line in self:
            if line.report_type == "restic":
                snaps = len(line.snapshot_ids)
                line.backup_ratio = f"{snaps}/{snaps}" if snaps else "-"
            elif line.container_count > 0:
                line.backup_ratio = f"{line.verified_file_count}/{line.container_count}"
            elif line.verified_file_count > 0:
                line.backup_ratio = f"{line.verified_file_count}/?"
            else:
                line.backup_ratio = "-"


class HostingBackupFile(models.Model):
    """Représente un fichier de sauvegarde individuel."""

    _name = "hosting.backup.file"
    _description = "Fichier de sauvegarde"
    _order = "line_id, name"

    line_id = fields.Many2one(
        comodel_name="hosting.backup.line",
        string="Ligne de sauvegarde",
        required=True,
        ondelete="cascade",
    )
    name = fields.Char(
        string="Nom de fichier",
        required=True,
    )
    size = fields.Char(
        string="Taille",
    )
    checksum = fields.Char(
        string="Somme de contrôle SHA256",
    )
    verified = fields.Boolean(
        string="Vérifié",
        default=False,
    )
