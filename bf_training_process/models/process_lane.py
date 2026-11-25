from odoo import api, fields, models


class BfProcessLane(models.Model):
    """Le couloir gagne les personnes qui jouent le rôle.

    Une carte de processus nomme un rôle et lui donne des étapes. Elle ne dit
    nulle part **qui** tient ce rôle, et c'est exactement ce qui manque pour
    qu'une exigence de formation puisse viser « tous ceux qui font ça » au lieu
    d'une liste de noms recopiée à la main, qui se périme au premier départ.
    """

    _inherit = "bf.process.lane"

    employee_ids = fields.Many2many(
        "hr.employee", "bf_process_lane_employee_rel", "lane_id", "employee_id",
        string="People who hold this role",
        help="Who plays this role today. A training requirement can "
             "target the lane rather than copy out the names.")
    employee_count = fields.Integer(
        string="Number of people", compute="_compute_employee_count")
    training_activity_count = fields.Integer(
        string="Number of training activities", compute="_compute_training_activity_count")

    @api.depends("employee_ids")
    def _compute_employee_count(self):
        for rec in self:
            rec.employee_count = len(rec.employee_ids)

    def _compute_training_activity_count(self):
        """Les formations accrochées aux étapes de ce couloir, sans doublon."""
        for rec in self:
            rec.training_activity_count = len(rec.node_ids.mapped("training_activity_ids"))

    def action_open_training_activities(self):
        self.ensure_one()
        activites = self.node_ids.mapped("training_activity_ids")
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Training activities for the lane"),
            "res_model": "bf.training.activity",
            "view_mode": "list,form",
            "domain": [("id", "in", activites.ids)],
        }
