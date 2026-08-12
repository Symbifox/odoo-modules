from odoo import fields, models


class ProjectTask(models.Model):
    _inherit = "project.task"

    state = fields.Selection(
        selection_add=[
            ("05_waiting_client", "Attente - Client"),
            ("06_waiting_external", "Attente - Externe"),
        ],
        ondelete={
            "05_waiting_client": "set default",
            "06_waiting_external": "set default",
        },
    )
