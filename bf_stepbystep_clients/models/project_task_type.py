from odoo import fields, models


class ProjectTaskType(models.Model):
    _inherit = "project.task.type"

    progression_step_number = fields.Integer(
        string="Progression step number",
        help="Step number in the engagement's linear progression. Leave "
             "at 0 (default) to exclude this stage from the Step-by-Step "
             "view.",
    )
    progression_step_name = fields.Char(
        string="Progression step name",
        translate=True,
        help="Short label shown in the linear view (e.g. \"Kickoff\", "
             "\"Initial audit\"). If empty, the stage name is used.",
    )
    progression_client_visible = fields.Boolean(
        string="Visible on client portal",
        default=True,
        help="Check to show this stage on the client's public portal. "
             "Uncheck for internal stages (e.g. internal review, "
             "invoicing).",
    )
    progression_client_action_hint = fields.Text(
        string="Client instruction",
        translate=True,
        help="Instruction shown to the client on the portal when the "
             "project reaches this stage (e.g. \"Upload your forms X and "
             "Y\").",
    )
