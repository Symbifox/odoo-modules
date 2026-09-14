from odoo import fields, models


class BfTimerPinnedTask(models.Model):
    _name = "bf.timer.pinned.task"
    _description = "Pinned task for the timer"
    _order = "sequence, id"

    user_id = fields.Many2one(
        "res.users", required=True, default=lambda self: self.env.uid, index=True,
    )
    task_id = fields.Many2one(
        "project.task", required=True, index=True, ondelete="cascade",
    )
    sequence = fields.Integer(default=10)

    _sql_constraints = [
        ("user_task_unique", "UNIQUE(user_id, task_id)", "This task is "
                                                         "already pinned."),
    ]
