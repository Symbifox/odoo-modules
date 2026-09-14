from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    bf_timer_rounding_mode = fields.Selection(
        [
            ("none", "No rounding"),
            ("round_all", "Always round"),
            ("round_below_threshold", "Round below a threshold"),
        ],
        string="Rounding mode",
        config_parameter="bf_timer.rounding_mode",
        default="round_all",
    )
    bf_timer_rounding_increment = fields.Selection(
        [
            ("1", "1 minute"),
            ("5", "5 minutes"),
            ("10", "10 minutes"),
            ("15", "15 minutes"),
        ],
        string="Rounding increment",
        config_parameter="bf_timer.rounding_increment",
        default="5",
    )
    bf_timer_rounding_threshold = fields.Integer(
        string="Rounding threshold (minutes)",
        config_parameter="bf_timer.rounding_threshold",
        default=30,
        help="Round only if the raw duration is below this threshold (in "
             "minutes).",
    )
