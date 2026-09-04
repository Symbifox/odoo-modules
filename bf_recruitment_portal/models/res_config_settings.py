# Part of bf_recruitment_portal. Voir LICENSE.
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    recruitment_portal_book_enabled = fields.Boolean(
        related="company_id.recruitment_portal_book_enabled", readonly=False,
    )
    recruitment_portal_otp_required = fields.Boolean(
        related="company_id.recruitment_portal_otp_required", readonly=False,
    )
