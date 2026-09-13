from odoo import api, fields, models


class BfTrainingActivity(models.Model):
    _inherit = "bf.training.activity"

    process_node_ids = fields.Many2many(
        "bf.process.node", "bf_training_activity_node_rel", "activity_id", "node_id",
        string="Étapes de processus enseignées",
        help="Les gestes que cette formation apprend à tenir. Une formation peut "
             "couvrir plusieurs étapes, et une étape peut en demander plusieurs.")
    process_node_count = fields.Integer(
        string="Nombre d'étapes", compute="_compute_process_node_count")
    process_lane_ids = fields.Many2many(
        "bf.process.lane", string="Couloirs concernés",
        compute="_compute_process_lane_ids",
        help="Les rôles qui tiennent au moins une des étapes enseignées.")

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
            "name": "Étapes enseignées",
            "res_model": "bf.process.node",
            "view_mode": "list,form",
            "domain": [("id", "in", self.process_node_ids.ids)],
        }
