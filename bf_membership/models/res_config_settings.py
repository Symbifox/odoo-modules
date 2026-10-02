from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    membership_auto_renewal = fields.Boolean(
        related="company_id.membership_auto_renewal", readonly=False)
    membership_reminders = fields.Boolean(
        related="company_id.membership_reminders", readonly=False)
    membership_reminder_first_days = fields.Integer(
        related="company_id.membership_reminder_first_days", readonly=False)
    membership_reminder_second_days = fields.Integer(
        related="company_id.membership_reminder_second_days", readonly=False)
