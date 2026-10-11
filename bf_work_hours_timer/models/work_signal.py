from odoo import fields, models


class WorkSignal(models.Model):
    _inherit = "bf.work.signal"

    source = fields.Selection(selection_add=[("timer", "Timer")], ondelete={"timer": "cascade"})
    bf_timer_id = fields.Many2one("bf.timer", string="Timer segment", readonly=True, index="btree_not_null",
                                  ondelete="set null")
