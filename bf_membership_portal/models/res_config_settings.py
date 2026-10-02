from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    membership_directory = fields.Selection(
        related="company_id.membership_directory", readonly=False, required=True)
