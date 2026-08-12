from odoo import fields, models


class HourBankThresholdEvent(models.Model):
    _name = 'hour.bank.threshold.event'
    _description = "Alerte de palier de banque d'heures (historique)"
    _order = 'fired_date desc, id desc'

    bank_id = fields.Many2one(
        'hour.bank.client', required=True, ondelete='cascade',
        index=True, string="Banque d'heures",
    )
    threshold_line_id = fields.Many2one(
        'hour.bank.threshold.line', ondelete='set null',
        string="Palier",
    )
    mode_snapshot = fields.Selection([
        ('unbilled', 'Heures non facturées'),
        ('budget_pct', '% du budget alloué'),
        ('balance_floor', 'Solde résiduel sous seuil'),
    ], string="Mode au moment du déclenchement", readonly=True)
    value_snapshot = fields.Float(
        readonly=True, string="Valeur du palier",
    )
    measured_value = fields.Float(
        readonly=True, string="Valeur mesurée",
    )
    period_key = fields.Char(
        index=True, readonly=True, string="Clé de période",
    )
    fired_date = fields.Datetime(
        default=fields.Datetime.now, readonly=True, string="Envoyé le",
    )
    recipient_ids = fields.Many2many(
        'res.partner', 'hour_bank_threshold_event_recipient_rel',
        string="Destinataires", readonly=True,
    )
    attachment_id = fields.Many2one(
        'ir.attachment', ondelete='set null', readonly=True,
        string="Pièce jointe XLSX",
    )
    mail_message_id = fields.Many2one(
        'mail.message', ondelete='set null', readonly=True,
        string="Message chatter",
    )
