# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from datetime import timedelta

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models


class HostingMaintenanceSchedule(models.Model):
    _name = "hosting.maintenance.schedule"
    _description = "Planification de maintenance d'hébergement"
    _order = "next_due, service_id"
    _inherit = ["mail.thread", "mail.activity.mixin"]

    name = fields.Char(
        string="Nom de la tâche",
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
            ("security_patch", "Correctifs de sécurité"),
            ("db_optimize", "Optimisation de base de données"),
            ("backup_verify", "Vérification de sauvegarde"),
            ("backup_setup", "Configuration de sauvegarde"),
            ("log_cleanup", "Nettoyage/Rotation des journaux"),
            ("container_update", "Mise à jour d'image de conteneur"),
            ("ssl_renewal", "Renouvellement de certificat SSL"),
            ("storage_cleanup", "Nettoyage de stockage"),
            ("performance_review", "Revue de performance"),
            ("app_update", "Mises à jour d'application"),
            ("admin_check", "Vérification du panneau d'administration"),
            ("security_audit", "Audit de sécurité"),
            ("other", "Autre (personnalisé)"),
        ],
        string="Type de maintenance",
        required=True,
        default="other",
        tracking=True,
    )
    frequency = fields.Selection(
        selection=[
            ("weekly", "Hebdomadaire"),
            ("biweekly", "Aux 2 semaines"),
            ("monthly", "Mensuel"),
            ("quarterly", "Trimestriel"),
            ("biannually", "Semestriel"),
            ("yearly", "Annuel"),
        ],
        string="Fréquence",
        required=True,
        default="quarterly",
        tracking=True,
    )
    template_line_id = fields.Many2one(
        comodel_name="hosting.maintenance.template.line",
        string="Depuis le modèle",
        readonly=True,
        help="La ligne de modèle à partir de laquelle cette planification a été créée",
    )
    last_performed = fields.Date(
        string="Dernière exécution",
        tracking=True,
        help="Date de la dernière exécution de cette tâche de maintenance",
    )
    next_due = fields.Date(
        string="Prochaine échéance",
        compute="_compute_next_due",
        store=True,
        help="Date calculée pour la prochaine maintenance planifiée",
    )
    days_until_due = fields.Integer(
        string="Jours avant l'échéance",
        compute="_compute_days_until_due",
        store=True,
    )
    is_overdue = fields.Boolean(
        string="En retard",
        compute="_compute_is_overdue",
        store=True,
    )
    user_id = fields.Many2one(
        comodel_name="res.users",
        string="Assigné à",
        default=lambda self: self.env.user,
        tracking=True,
    )
    instructions = fields.Text(
        string="Instructions",
        help="Instructions étape par étape pour effectuer cette tâche de maintenance",
    )
    notes = fields.Text(
        string="Notes",
        help="Notes ou commentaires supplémentaires sur cette planification de maintenance",
    )
    active = fields.Boolean(
        string="Actif",
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
            # Construire la note d'activité avec les détails
            note_parts = [
                f"<strong>{record.name}</strong>",
                f"Service : {record.service_id.name}",
                f"Client : {record.service_id.partner_id.name or 'N/D'}",
                f"Type : {record._get_type_display()}",
                f"Fréquence : {record._get_frequency_display()}",
            ]
            if record.instructions:
                note_parts.append(f"<br/><strong>Instructions :</strong><br/>{record.instructions.replace(chr(10), '<br/>')}")
            note = "<br/>".join(note_parts)
            # Créer une nouvelle activité
            record.activity_schedule(
                "hosting_management.mail_activity_type_hosting_maintenance",
                date_deadline=record.next_due,
                summary=record.name,
                note=note,
                user_id=record.user_id.id or self.env.user.id,
            )

    @api.depends("last_performed", "frequency")
    def _compute_next_due(self):
        """Calculer la prochaine date d'échéance selon la fréquence et la dernière exécution."""
        frequency_deltas = {
            "weekly": relativedelta(weeks=1),
            "biweekly": relativedelta(weeks=2),
            "monthly": relativedelta(months=1),
            "quarterly": relativedelta(months=3),
            "biannually": relativedelta(months=6),
            "yearly": relativedelta(years=1),
        }
        today = fields.Date.today()

        for record in self:
            if record.last_performed and record.frequency:
                delta = frequency_deltas.get(record.frequency, relativedelta(months=3))
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

    def action_mark_done(self):
        """Marquer la tâche de maintenance comme complétée et recalculer la prochaine échéance."""
        today = fields.Date.today()
        activity_type = self.env.ref(
            "hosting_management.mail_activity_type_hosting_maintenance",
            raise_if_not_found=False,
        )

        AuditLog = self.env["hosting.audit.log"]
        for record in self:
            AuditLog._log_event(
                action_type="maintenance",
                category="ops",
                description=(
                    f"Maintenance complétée : {record.name} "
                    f"(service : {record.service_id.name})"
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
                activities.action_feedback(feedback=f"Complété le {today}")
            # Publier un message dans le chatter
            record.message_post(
                body=f"Tâche de maintenance complétée le {today}",
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
            ("service_id.state", "=", "active"),
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
            ("service_id.state", "=", "active"),
        ], order="next_due, service_id")

    def _get_type_display(self):
        """Obtenir la valeur d'affichage du type de maintenance."""
        self.ensure_one()
        return dict(self._fields["maintenance_type"].selection).get(
            self.maintenance_type, self.maintenance_type
        )

    def _get_frequency_display(self):
        """Obtenir la valeur d'affichage de la fréquence."""
        self.ensure_one()
        return dict(self._fields["frequency"].selection).get(
            self.frequency, self.frequency
        )
