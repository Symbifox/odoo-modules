from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    bf_timer_rounding_mode = fields.Selection(
        [
            ("none", "Aucun arrondi"),
            ("round_all", "Arrondir toujours"),
            ("round_below_threshold", "Arrondir sous un seuil"),
        ],
        string="Mode d'arrondi",
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
        string="Incr\u00e9ment d'arrondi",
        config_parameter="bf_timer.rounding_increment",
        default="5",
    )
    bf_timer_rounding_threshold = fields.Integer(
        string="Seuil d'arrondi (minutes)",
        config_parameter="bf_timer.rounding_threshold",
        default=30,
        help="Arrondir uniquement si la dur\u00e9e brute est inf\u00e9rieure \u00e0 ce seuil (en minutes).",
    )
