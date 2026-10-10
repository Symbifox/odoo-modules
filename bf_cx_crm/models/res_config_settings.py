"""Bridge setting: post-loss survey program."""
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    bf_cx_loss_program_id = fields.Many2one(
        "bf.cx.program",
        string="Lost-deal program",
        config_parameter="bf_cx.loss_program_id",
        help="Program whose survey is sent to the contact when an "
             "opportunity is marked lost. Empty = nothing is sent.",
    )
    bf_cx_won_program_id = fields.Many2one(
        "bf.cx.program",
        string="Won-deal enrollment program",
        config_parameter="bf_cx.won_program_id",
        help="When an opportunity is won, the customer is added to the "
             "next DRAFT wave of this program (nothing is sent right "
             "away). Empty = disabled.",
    )
