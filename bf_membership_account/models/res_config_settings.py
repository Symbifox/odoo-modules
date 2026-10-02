from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    membership_charity_number = fields.Char(
        related="company_id.membership_charity_number", readonly=False)
    membership_receipt_signer = fields.Char(
        related="company_id.membership_receipt_signer", readonly=False)
    membership_receipt_signer_title = fields.Char(
        related="company_id.membership_receipt_signer_title", readonly=False)
    membership_receipt_signature = fields.Image(
        related="company_id.membership_receipt_signature", readonly=False,
        groups="base.group_system,bf_membership.group_membership_manager")
    membership_receipt_place = fields.Char(
        related="company_id.membership_receipt_place", readonly=False)
