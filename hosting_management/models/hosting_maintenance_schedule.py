# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from datetime import timedelta

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models

from .hosting_lang import reader_env, user_env

FREQUENCY_DELTAS = {
    "weekly": relativedelta(weeks=1),
    "biweekly": relativedelta(weeks=2),
    "monthly": relativedelta(months=1),
    "quarterly": relativedelta(months=3),
    "biannually": relativedelta(months=6),
    "yearly": relativedelta(years=1),
}


class HostingMaintenanceSchedule(models.Model):
    _name = "hosting.maintenance.schedule"
    _description = "Hosting maintenance schedule"
    _order = "next_due, service_id"
    _inherit = ["mail.thread", "mail.activity.mixin"]

    name = fields.Char(
        string="Task name",
        required=True,
        tracking=True,
    )
    service_id = fields.Many2one(
        comodel_name="hosting.service",
        string="Service",
        required=True,
        ondelete="cascade",
        tracking=True,
    )
    partner_id = fields.Many2one(
        related="service_id.partner_id",
        string="Client",
        store=True,
    )
    maintenance_type = fields.Selection(
        selection=[
            ("security_patch", "Security patches"),
            ("db_optimize", "Database optimization"),
            ("backup_verify", "Backup verification"),
            ("backup_setup", "Backup setup"),
            ("log_cleanup", "Log cleanup/rotation"),
            ("container_update", "Container image update"),
            ("ssl_renewal", "SSL certificate renewal"),
            ("storage_cleanup", "Storage cleanup"),
            ("performance_review", "Performance review"),
            ("app_update", "Application updates"),
            ("admin_check", "Admin panel check"),
            ("security_audit", "Security audit"),
            ("other", "Other (custom)"),
        ],
        string="Maintenance type",
        required=True,
        default="other",
        tracking=True,
    )
    frequency = fields.Selection(
        selection=[
            ("weekly", "Weekly"),
            ("biweekly", "Every 2 weeks"),
            ("monthly", "Monthly"),
            ("quarterly", "Quarterly"),
            ("biannually", "Semiannual"),
            ("yearly", "Annual"),
        ],
        string="Frequency",
        required=True,
        default="quarterly",
        tracking=True,
    )
    template_line_id = fields.Many2one(
        comodel_name="hosting.maintenance.template.line",
        string="From template",
        readonly=True,
        help="The template line this schedule was created from",
    )
    last_performed = fields.Date(
        string="Last run",
        tracking=True,
        help="Date this maintenance task was last performed",
    )
    next_due = fields.Date(
        string="Next due date",
        compute="_compute_next_due",
        store=True,
        help="Computed date of the next scheduled maintenance",
    )
    days_until_due = fields.Integer(
        string="Days until due",
        compute="_compute_days_until_due",
        store=True,
    )
    is_overdue = fields.Boolean(
        string="Overdue",
        compute="_compute_is_overdue",
        store=True,
    )
    user_id = fields.Many2one(
        comodel_name="res.users",
        string="Assigned to",
        default=lambda self: self.env.user,
        tracking=True,
    )
    instructions = fields.Text(
        string="Instructions",
        help="Step-by-step instructions to perform this maintenance task",
    )
    notes = fields.Text(
        string="Notes",
        help="Additional notes or comments about this maintenance schedule",
    )
    active = fields.Boolean(
        string="Active",
        default=True,
        tracking=True,
    )

    @api.model_create_multi
    def create(self, vals_list):
        """Créer les planifications de maintenance et leurs activités associées."""
        records = super().create(vals_list)
        records._create_maintenance_activity()
        return records

    def _create_maintenance_activity(self):
        """Créer une activité pour la planification de maintenance."""
        activity_type = self.env.ref(
            "hosting_management.mail_activity_type_hosting_maintenance",
            raise_if_not_found=False,
        )
        if not activity_type:
            return

        for record in self:
            if not record.next_due or not record.active:
                continue
            # Supprimer les activités de maintenance existantes pour cet enregistrement
            record.activity_ids.filtered(
                lambda a: a.activity_type_id == activity_type
            ).unlink()
            # Note dans la langue de l'assigné, activité créée dans son env
            # (objet de l'avis d'activité, piège 63) ; libellés de sélection
            # traduits sur le même env.
            assignee = record.user_id or self.env.user
            aenv = user_env(self.env, assignee)
            arecord = record.with_env(aenv)
            note_parts = [
                f"<strong>{record.name}</strong>",
                aenv._("Service: %s", record.service_id.name),
                aenv._("Client: %s", record.service_id.partner_id.name or aenv._("N/A")),
                aenv._("Type: %s", arecord._get_type_display()),
                aenv._("Frequency: %s", arecord._get_frequency_display()),
            ]
            if record.instructions:
                note_parts.append(
                    "<br/><strong>%s</strong><br/>%s" % (
                        aenv._("Instructions:"),
                        record.instructions.replace(chr(10), '<br/>'),
                    )
                )
            note = "<br/>".join(note_parts)
            # Créer une nouvelle activité
            arecord.activity_schedule(
                "hosting_management.mail_activity_type_hosting_maintenance",
                date_deadline=record.next_due,
                summary=record.name,
                note=note,
                user_id=assignee.id,
            )

    # ------------------------------------------------------------ crochets
    # Tous les lecteurs de ce modèle (tableau de bord, résumé, alertes mobiles,
    # échéancier) passent par ces trois méthodes au lieu de recopier leur
    # propre filtre. Il y en avait onze copies de
    # ("service_id.state", "=", "active"), et une planification qui vise une
    # machine plutôt qu'un service (bf_hosting_patch) n'a pas de service, donc
    # ces onze lecteurs l'écartaient en silence : des correctifs de sécurité en
    # retard manquaient au compte du tableau de bord.

    @api.model
    def _target_active_domain(self):
        """Domaine : la cible de la planification est en service.

        Le module de base ne connaît que les services. Un module qui ajoute une
        autre sorte de cible étend ce domaine au lieu de le remplacer.
        """
        return [("service_id.state", "=", "active")]

    def _target_display(self):
        """(libellé, code) de ce que la planification vise, pour l'affichage."""
        self.ensure_one()
        return self.service_id.name or "", self.service_id.code or ""

    @api.model
    def _frequency_delta(self, frequency):
        """L'intervalle d'une fréquence, le même pour le calcul et la projection."""
        return FREQUENCY_DELTAS.get(frequency, relativedelta(months=3))

    @api.depends("last_performed", "frequency")
    def _compute_next_due(self):
        """Calculer la prochaine date d'échéance selon la fréquence et la dernière exécution."""
        today = fields.Date.today()

        for record in self:
            if record.last_performed and record.frequency:
                delta = self._frequency_delta(record.frequency)
                record.next_due = record.last_performed + delta
            elif record.frequency:
                # Si jamais exécuté, fixer la prochaine échéance à aujourd'hui
                record.next_due = today
            else:
                record.next_due = False

    @api.depends("next_due")
    def _compute_days_until_due(self):
        """Calculer le nombre de jours avant l'échéance de la tâche."""
        today = fields.Date.today()
        for record in self:
            if record.next_due:
                record.days_until_due = (record.next_due - today).days
            else:
                record.days_until_due = 0

    @api.depends("next_due")
    def _compute_is_overdue(self):
        """Déterminer si la tâche de maintenance est en retard."""
        today = fields.Date.today()
        for record in self:
            record.is_overdue = record.next_due and record.next_due < today

    @api.model
    def _cron_refresh_due_indicators(self):
        """Recalculer les indicateurs qui dépendent de la date du jour.

        ``days_until_due`` et ``is_overdue`` sont stockés et ne dépendent que de
        ``next_due``. Odoo ne les réécrit donc qu'au moment où l'échéance bouge,
        c'est-à-dire quand la tâche est marquée faite : entre deux exécutions le
        calendrier avance et les valeurs restent figées à ce qu'elles valaient ce
        jour-là. Sans ce passage périodique, ``days_until_due`` affiche en
        permanence la longueur du cycle et ``is_overdue`` ne devient jamais vrai.

        ⚠️ Le recalcul passe par ``add_to_compute`` et non par un appel direct
        aux méthodes ``_compute_*``. Affecter un champ stocké sur des fiches
        réelles NON protégées prend le chemin « logique métier complète » de
        ``Field.__set__`` : chaque affectation devient un ``write()`` d'une seule
        fiche, qui traverse ``mail.thread.write`` et émet son propre UPDATE. Un
        appel direct coûte donc deux ``write()`` par planification à chaque
        passage. ``add_to_compute`` marque les champs à recalculer et laisse
        ``flush_all`` le faire dans le contexte protégé, ce qui groupe l'écriture
        et n'active aucune logique de suivi.
        """
        schedules = self.with_context(active_test=False).search([])
        if not schedules:
            return
        for fname in ("days_until_due", "is_overdue"):
            self.env.add_to_compute(self._fields[fname], schedules)
        self.env.flush_all()

    def action_mark_done(self):
        """Marquer la tâche de maintenance comme complétée et recalculer la prochaine échéance."""
        today = fields.Date.today()
        activity_type = self.env.ref(
            "hosting_management.mail_activity_type_hosting_maintenance",
            raise_if_not_found=False,
        )

        AuditLog = self.env["hosting.audit.log"]
        # Journal d'audit, retour d'activité et note au fil : relus par l'équipe
        # d'hébergement, langue de la société (bouton, MCP ou XML-RPC).
        env = reader_env(self.env)
        for record in self:
            AuditLog._log_event(
                action_type="maintenance",
                category="ops",
                description=env._(
                    "Maintenance completed: %(name)s (service: %(service)s)",
                    name=record.name, service=record.service_id.name,
                ),
                res_model=self._name,
                res_id=record.id,
                res_name=record.display_name,
                service_id=record.service_id.id,
                server_id=(
                    record.service_id.server_id.id
                    if record.service_id.server_id else None
                ),
            )
            record.last_performed = today
            # Marquer les activités de maintenance existantes comme terminées
            if activity_type:
                activities = record.activity_ids.filtered(
                    lambda a: a.activity_type_id == activity_type
                )
                activities.action_feedback(feedback=env._("Completed on %s", today))
            # Publier un message dans le chatter
            record.message_post(
                body=env._("Maintenance task completed on %s", today),
                message_type="notification",
            )
        # Créer de nouvelles activités pour la prochaine échéance (après recalcul de next_due)
        self._create_maintenance_activity()
        return True

    @api.model
    def _get_due_schedules(self, days_ahead=14):
        """Obtenir les planifications de maintenance dues dans le nombre de jours spécifié.

        Args:
            days_ahead: Nombre de jours à anticiper (par défaut 14)

        Returns:
            Jeu d'enregistrements des planifications de maintenance dues dans la période spécifiée
        """
        today = fields.Date.today()
        cutoff_date = today + timedelta(days=days_ahead)

        return self.search([
            ("active", "=", True),
            ("next_due", "<=", cutoff_date),
            *self._target_active_domain(),
        ], order="next_due, service_id")

    @api.model
    def _get_overdue_schedules(self):
        """Obtenir toutes les planifications de maintenance en retard.

        Returns:
            Jeu d'enregistrements des planifications de maintenance en retard
        """
        today = fields.Date.today()

        return self.search([
            ("active", "=", True),
            ("next_due", "<", today),
            *self._target_active_domain(),
        ], order="next_due, service_id")

    def _get_type_display(self):
        """Obtenir la valeur d'affichage du type de maintenance."""
        self.ensure_one()
        # Libellé traduit dans la langue de self.env (l'appelant l'épingle).
        return dict(
            self._fields["maintenance_type"]._description_selection(self.env)
        ).get(self.maintenance_type, self.maintenance_type)

    def _get_frequency_display(self):
        """Obtenir la valeur d'affichage de la fréquence."""
        self.ensure_one()
        return dict(
            self._fields["frequency"]._description_selection(self.env)
        ).get(self.frequency, self.frequency)
