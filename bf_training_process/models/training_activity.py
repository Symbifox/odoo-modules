from odoo import api, fields, models


class BfTrainingActivity(models.Model):
    _inherit = "bf.training.activity"

    process_node_ids = fields.Many2many(
        "bf.process.node", "bf_training_activity_node_rel", "activity_id", "node_id",
        string="Process steps taught",
        help="The tasks this training teaches people to carry out. One "
             "training activity can cover several steps, and one step can "
             "require several activities.")
    process_node_count = fields.Integer(
        string="Number of steps", compute="_compute_process_node_count")
    process_lane_ids = fields.Many2many(
        "bf.process.lane", string="Lanes involved",
        compute="_compute_process_lane_ids",
        help="The roles that carry out at least one of the steps taught.")

    @api.depends("process_node_ids")
    def _compute_process_node_count(self):
        for rec in self:
            rec.process_node_count = len(rec.process_node_ids)

    @api.depends("process_node_ids.lane_id")
    def _compute_process_lane_ids(self):
        for rec in self:
            rec.process_lane_ids = rec.process_node_ids.mapped("lane_id")

    def action_open_process_nodes(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Steps taught"),
            "res_model": "bf.process.node",
            "view_mode": "list,form",
            "domain": [("id", "in", self.process_node_ids.ids)],
        }
