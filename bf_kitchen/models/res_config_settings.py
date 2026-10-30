from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    bf_kitchen_target_pct = fields.Float(related="company_id.bf_kitchen_target_pct", readonly=False)
    bf_kitchen_price_rounding = fields.Selection(related="company_id.bf_kitchen_price_rounding", readonly=False)
    bf_kitchen_cost_max_age = fields.Integer(related="company_id.bf_kitchen_cost_max_age", readonly=False)
    bf_kitchen_alert_points = fields.Float(related="company_id.bf_kitchen_alert_points", readonly=False)
    bf_kitchen_manager_id = fields.Many2one(related="company_id.bf_kitchen_manager_id", readonly=False)
    bf_kitchen_tax_nonrecoverable_pct = fields.Float(
        related="company_id.bf_kitchen_tax_nonrecoverable_pct", readonly=False)
    bf_kitchen_receive_without_po = fields.Boolean(
        related="company_id.bf_kitchen_receive_without_po", readonly=False)
