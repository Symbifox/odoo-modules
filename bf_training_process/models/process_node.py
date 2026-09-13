from odoo import api, fields, models


class BfProcessNode(models.Model):
    """L'étape gagne les formations qu'il faut avoir pour la tenir."""

    _inherit = "bf.process.node"

    training_activity_ids = fields.Many2many(
        "bf.training.activity", "bf_training_activity_node_rel", "node_id", "activity_id",
        string="Formations exigées par cette étape")
    training_activity_count = fields.Integer(
        string="Nombre de formations", compute="_compute_training_activity_count")

    @api.depends("training_activity_ids")
    def _compute_training_activity_count(self):
        for rec in self:
            rec.training_activity_count = len(rec.training_activity_ids)

    def action_open_training_activities(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Formations de l'étape",
            "res_model": "bf.training.activity",
            "view_mode": "list,form",
            "domain": [("id", "in", self.training_activity_ids.ids)],
            "context": {"default_process_node_ids": [(6, 0, self.ids)]},
        }
