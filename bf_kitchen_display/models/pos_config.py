from odoo import api, fields, models


class PosConfig(models.Model):
    _inherit = "pos.config"

    bf_kitchen_station_ids = fields.Many2many(
        "bf.kitchen.station", "bf_kitchen_station_pos_config_rel", "pos_config_id", "station_id",
        string="Kitchen stations", readonly=True)
    bf_kitchen_display = fields.Boolean(
        "Kitchen display", compute="_compute_bf_kitchen_display",
        help="A kitchen station shows this register's orders: Order sends them at once.")

    @api.depends("bf_kitchen_station_ids.active")
    def _compute_bf_kitchen_display(self):
        for config in self:
            config.bf_kitchen_display = bool(config.sudo().bf_kitchen_station_ids)
