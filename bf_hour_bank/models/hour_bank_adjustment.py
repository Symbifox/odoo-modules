from odoo import api, fields, models


class HourBankAdjustment(models.Model):
    _name = 'hour.bank.adjustment'
    _description = "Ajustement manuel de banque d'heures"
    _order = 'date desc, id desc'

    hour_bank_id = fields.Many2one(
        'hour.bank.client', required=True, string="Banque d'heures",
        ondelete='cascade',
    )
    date = fields.Date(required=True, default=fields.Date.today)
    hours = fields.Float(
        required=True, string="Heures",
        help="Positif = cr\u00e9dit (ajout d'heures), N\u00e9gatif = d\u00e9bit (retrait).",
    )
    description = fields.Char(required=True, string="Description")
    partner_id = fields.Many2one(
        related='hour_bank_id.partner_id', store=True, string="Client",
    )
