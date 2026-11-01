from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    pos_bf_host = fields.Boolean(related="pos_config_id.bf_host", readonly=False)
    pos_bf_host_employee_ids = fields.Many2many(related="pos_config_id.bf_host_employee_ids", readonly=False)
    pos_bf_host_double_seat_minutes = fields.Integer(related="pos_config_id.bf_host_double_seat_minutes",
                                                     readonly=False)
    pos_bf_host_pace_covers = fields.Integer(related="pos_config_id.bf_host_pace_covers", readonly=False)
    pos_bf_host_ticket_alert_minutes = fields.Integer(related="pos_config_id.bf_host_ticket_alert_minutes",
                                                      readonly=False)
    pos_bf_host_turn_minutes = fields.Integer(related="pos_config_id.bf_host_turn_minutes", readonly=False)
