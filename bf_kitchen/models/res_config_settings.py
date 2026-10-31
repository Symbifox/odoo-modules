from odoo import _, fields, models
from odoo.exceptions import AccessError


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
    bf_kitchen_count_frequency = fields.Selection(
        related="company_id.bf_kitchen_count_frequency", readonly=False)
    bf_kitchen_warehouse_id = fields.Many2one(related="company_id.bf_kitchen_warehouse_id", readonly=False)

    def action_bf_kitchen_create_locations(self):
        if not self.env.user.has_group("bf_kitchen.group_kitchen_manager"):
            raise AccessError(_("Only a kitchen manager can create the kitchen locations."))
        warehouses = self.env["stock.warehouse"].search([("company_id", "=", self.company_id.id)])
        created = warehouses._bf_kitchen_create_locations()
        return {
            "type": "ir.actions.client", "tag": "display_notification",
            "params": {"type": "success", "sticky": False,
                       "message": _("%s kitchen location(s) created.", len(created)) if created
                       else _("Every warehouse already has its kitchen locations.")},
        }
