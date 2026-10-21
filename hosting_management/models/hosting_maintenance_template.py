# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import api, fields, models


class HostingMaintenanceTemplate(models.Model):
    _name = "hosting.maintenance.template"
    _description = "Hosting maintenance template"
    _order = "sequence, name"

    name = fields.Char(
        string="Template name",
        required=True,
    )
    sequence = fields.Integer(
        string="Sequence",
        default=10,
    )
    software_id = fields.Many2one(
        comodel_name="hosting.software",
        string="Software",
        ondelete="cascade",
        help="If set, this template applies only to this software. Leave "
             "empty for a default template that applies to all services.",
    )
    is_default = fields.Boolean(
        string="Default template",
        compute="_compute_is_default",
        store=True,
        help="True if this template applies to all software (no software "
             "specified)",
    )
    line_ids = fields.One2many(
        comodel_name="hosting.maintenance.template.line",
        inverse_name="template_id",
        string="Maintenance tasks",
        copy=True,
    )
    task_count = fields.Integer(
        string="Tasks",
        compute="_compute_task_count",
    )
    active = fields.Boolean(
        string="Active",
        default=True,
    )
    notes = fields.Text(
        string="Notes",
        help="Additional notes about this maintenance template",
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
            "name": self.env._("Tasks - %s", self.name),
            "res_model": "hosting.maintenance.template.line",
            "views": [[False, "list"], [False, "form"]],
            "domain": [("template_id", "=", self.id)],
            "context": {"default_template_id": self.id},
        }


class HostingMaintenanceTemplateLine(models.Model):
    _name = "hosting.maintenance.template.line"
    _description = "Hosting maintenance template line"
    _order = "sequence, id"

    template_id = fields.Many2one(
        comodel_name="hosting.maintenance.template",
        string="Template",
        required=True,
        ondelete="cascade",
    )
    sequence = fields.Integer(
        string="Sequence",
        default=10,
    )
    name = fields.Char(
        string="Task name",
        required=True,
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
    )
    frequency = fields.Selection(
        selection=[
            ("once", "Once (on activation)"),
            ("weekly", "Weekly"),
            ("biweekly", "Every 2 weeks"),
            ("monthly", "Monthly"),
            ("quarterly", "Quarterly"),
            ("biannually", "Semiannual"),
            ("yearly", "Annual"),
        ],
        string="Frequency",
        required=True,
        default="monthly",
    )
    days_after_activation = fields.Integer(
        string="Days after activation",
        default=0,
        help="For the \"Once\" frequency: number of days after the "
             "service is activated when this task becomes due. Set to 0 "
             "for immediately.",
    )
    create_activity = fields.Boolean(
        string="Create an activity",
        default=False,
        help="If checked, an Odoo activity is created in addition to the "
             "maintenance schedule (useful for one-time setup tasks).",
    )
    instructions = fields.Text(
        string="Instructions",
        help="Step-by-step instructions to perform this maintenance task",
    )
    active = fields.Boolean(
        string="Active",
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
