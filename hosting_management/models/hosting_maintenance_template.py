# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import api, fields, models


class HostingMaintenanceTemplate(models.Model):
    _name = "hosting.maintenance.template"
    _description = "Modèle de maintenance d'hébergement"
    _order = "sequence, name"

    name = fields.Char(
        string="Nom du modèle",
        required=True,
    )
    sequence = fields.Integer(
        string="Séquence",
        default=10,
    )
    software_id = fields.Many2one(
        comodel_name="hosting.software",
        string="Logiciel",
        ondelete="cascade",
        help="Si défini, ce modèle s'applique uniquement à ce logiciel. "
             "Laisser vide pour un modèle par défaut applicable à tous les services.",
    )
    is_default = fields.Boolean(
        string="Modèle par défaut",
        compute="_compute_is_default",
        store=True,
        help="Vrai si ce modèle s'applique à tous les logiciels (aucun logiciel spécifié)",
    )
    line_ids = fields.One2many(
        comodel_name="hosting.maintenance.template.line",
        inverse_name="template_id",
        string="Tâches de maintenance",
        copy=True,
    )
    task_count = fields.Integer(
        string="Tâches",
        compute="_compute_task_count",
    )
    active = fields.Boolean(
        string="Actif",
        default=True,
    )
    notes = fields.Text(
        string="Notes",
        help="Notes supplémentaires sur ce modèle de maintenance",
    )

    @api.depends("software_id")
    def _compute_is_default(self):
        for record in self:
            record.is_default = not record.software_id

    def _compute_task_count(self):
        for record in self:
            record.task_count = len(record.line_ids)

    def action_view_tasks(self):
        """Voir les lignes de tâches de maintenance pour ce modèle."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": f"Tâches - {self.name}",
            "res_model": "hosting.maintenance.template.line",
            "views": [[False, "list"], [False, "form"]],
            "domain": [("template_id", "=", self.id)],
            "context": {"default_template_id": self.id},
        }


class HostingMaintenanceTemplateLine(models.Model):
    _name = "hosting.maintenance.template.line"
    _description = "Ligne de modèle de maintenance d'hébergement"
    _order = "sequence, id"

    template_id = fields.Many2one(
        comodel_name="hosting.maintenance.template",
        string="Modèle",
        required=True,
        ondelete="cascade",
    )
    sequence = fields.Integer(
        string="Séquence",
        default=10,
    )
    name = fields.Char(
        string="Nom de la tâche",
        required=True,
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
    )
    frequency = fields.Selection(
        selection=[
            ("once", "Une fois (à l'activation)"),
            ("weekly", "Hebdomadaire"),
            ("biweekly", "Aux 2 semaines"),
            ("monthly", "Mensuel"),
            ("quarterly", "Trimestriel"),
            ("biannually", "Semestriel"),
            ("yearly", "Annuel"),
        ],
        string="Fréquence",
        required=True,
        default="monthly",
    )
    days_after_activation = fields.Integer(
        string="Jours après l'activation",
        default=0,
        help="Pour la fréquence « Une fois » : nombre de jours après l'activation du service "
             "où cette tâche doit être due. Mettre à 0 pour immédiat.",
    )
    create_activity = fields.Boolean(
        string="Créer une activité",
        default=False,
        help="Si coché, une activité Odoo sera créée en plus de "
             "la planification de maintenance (utile pour les tâches de configuration ponctuelles).",
    )
    instructions = fields.Text(
        string="Instructions",
        help="Instructions étape par étape pour effectuer cette tâche de maintenance",
    )
    active = fields.Boolean(
        string="Actif",
        default=True,
    )

    def _get_frequency_for_schedule(self):
        """Convertir la fréquence du modèle en fréquence de planification de maintenance.

        Retourne la valeur de fréquence compatible avec hosting.maintenance.schedule,
        ou False pour les tâches ponctuelles.
        """
        self.ensure_one()
        frequency_map = {
            "once": False,
            "weekly": "monthly",
            "biweekly": "monthly",
            "monthly": "monthly",
            "quarterly": "quarterly",
            "biannually": "biannually",
            "yearly": "yearly",
        }
        return frequency_map.get(self.frequency, "quarterly")
