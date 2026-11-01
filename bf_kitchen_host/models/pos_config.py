from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class PosConfig(models.Model):
    _inherit = "pos.config"

    bf_host = fields.Boolean(
        "Host stand", help="The floor plan shows where each table is, the sections, the waiting list and "
                           "the kitchen's pace; the hosts below seat the guests.")
    bf_host_employee_ids = fields.Many2many(
        "hr.employee", "bf_host_employee_pos_config_rel", "pos_config_id", "employee_id", string="Hosts",
        domain="[('company_id', 'in', [company_id, False])]",
        help="Employees who seat the guests at this register, with their PIN: they see the floor plan only, "
             "never an order nor a payment. A manager keeps every right.")
    bf_host_section_ids = fields.One2many("bf.host.section", "config_id", string="Sections")
    bf_host_double_seat_minutes = fields.Integer(
        "Double seating (minutes)", default=10,
        help="A section seated again within this delay is a double seating: the host is warned.")
    bf_host_pace_covers = fields.Integer(
        "Covers per 15 minutes", default=0,
        help="What the kitchen can take in 15 minutes before its tickets pile up. 0: no ceiling.")
    bf_host_ticket_alert_minutes = fields.Integer(
        "Kitchen ticket alert (minutes)", default=20,
        help="A kitchen ticket still open after this delay turns the pace gauge red.")
    bf_host_turn_minutes = fields.Integer(
        "Usual meal length (minutes)", default=60,
        help="Used to quote a wait until this register has seen enough meals of each party size.")

    @api.constrains("bf_host_double_seat_minutes", "bf_host_pace_covers", "bf_host_ticket_alert_minutes",
                    "bf_host_turn_minutes")
    def _check_bf_host_delays(self):
        for config in self:
            if min(config.bf_host_double_seat_minutes, config.bf_host_pace_covers,
                   config.bf_host_ticket_alert_minutes) < 0 or config.bf_host_turn_minutes <= 0:
                raise ValidationError(_("The host stand's delays and ceiling cannot be negative, and the "
                                        "usual meal length must be above zero."))
