from odoo import fields, models


class ResCompany(models.Model):
    _inherit = 'res.company'

    unblock_notify_manager = fields.Boolean(
        string="Notify the project manager of unassigned tasks",
        default=True,
        help="When an unblocked task has nobody assigned, its project manager "
             "receives the notice instead of nobody.",
    )
    unblock_gen_plan = fields.Boolean(
        string="Add Gen's game plan to unblock notices",
        help="Gen reads the task, its thread and the tasks that unblocked it, "
             "and the notice leaves with a « Situation » and « Next actions ». "
             "The notice waits for the plan for about three minutes, then "
             "leaves without it.",
    )
