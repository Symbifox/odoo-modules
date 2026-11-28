from odoo import api, fields, models


class HourBankAdjustment(models.Model):
    _name = 'hour.bank.adjustment'
    _description = "Manual hour bank adjustment"
    _order = 'date desc, id desc'

    hour_bank_id = fields.Many2one(
        'hour.bank.client', required=True, string="Hour bank",
        ondelete='cascade',
    )
    date = fields.Date(required=True, default=fields.Date.today)
    hours = fields.Float(
        required=True, string="Hours",
        help="Positive = credit (hours added), Negative = debit (hours "
             "taken out).",
    )
    description = fields.Char(required=True, string="Description")
    partner_id = fields.Many2one(
        related='hour_bank_id.partner_id', store=True, string="Client",
    )
