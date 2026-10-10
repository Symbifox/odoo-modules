from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    bf_route_osrm_url = fields.Char(string="OSRM address", config_parameter="bf_route.osrm_url")
    bf_route_vroom_url = fields.Char(string="VROOM address", config_parameter="bf_route.vroom_url")
    bf_route_position_mode = fields.Selection(related="company_id.bf_route_position_mode",
                                              readonly=False)
    bf_route_notice = fields.Html(related="company_id.bf_route_notice", readonly=False)
    bf_route_position_days = fields.Integer(related="company_id.bf_route_position_days",
                                            readonly=False)
    bf_route_late_minutes = fields.Integer(related="company_id.bf_route_late_minutes",
                                           readonly=False)
