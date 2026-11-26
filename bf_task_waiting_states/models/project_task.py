from odoo import fields, models


class ProjectTask(models.Model):
    _inherit = "project.task"

    state = fields.Selection(
        selection_add=[
            ("05_waiting_client", "Waiting on Client"),
            ("06_waiting_external", "Waiting on External"),
        ],
        ondelete={
            "05_waiting_client": "set default",
            "06_waiting_external": "set default",
        },
    )
