"""Bridge setting: donor experience survey program."""
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    bf_cx_donor_program_id = fields.Many2one(
        "bf.cx.program",
        string="Donor experience program",
        config_parameter="bf_cx.donor_program_id",
        help="Program whose survey is sent to the donor when a donation is validated. A loyal donor gives often: the program's minimum cadence is the main safeguard, 90 days recommended. Empty = nothing sent.",
    )
