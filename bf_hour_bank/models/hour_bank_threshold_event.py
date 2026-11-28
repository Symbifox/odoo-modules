from odoo import fields, models


class HourBankThresholdEvent(models.Model):
    _name = 'hour.bank.threshold.event'
    _description = "Hour bank threshold alert (history)"
    _order = 'fired_date desc, id desc'

    bank_id = fields.Many2one(
        'hour.bank.client', required=True, ondelete='cascade',
        index=True, string="Hour bank",
    )
    threshold_line_id = fields.Many2one(
        'hour.bank.threshold.line', ondelete='set null',
        string="Threshold line",
    )
    mode_snapshot = fields.Selection([
        ('unbilled', "Unbilled hours"),
        ('budget_pct', "% of allocated budget"),
        ('balance_floor', "Remaining balance below a floor"),
    ], string="Mode at the time of the trigger", readonly=True)
    value_snapshot = fields.Float(
        readonly=True, string="Threshold value",
    )
    measured_value = fields.Float(
        readonly=True, string="Measured value",
    )
    period_key = fields.Char(
        index=True, readonly=True, string="Period key",
    )
    fired_date = fields.Datetime(
        default=fields.Datetime.now, readonly=True, string="Sent on",
    )
    recipient_ids = fields.Many2many(
        'res.partner', 'hour_bank_threshold_event_recipient_rel',
        string="Recipients", readonly=True,
    )
    attachment_id = fields.Many2one(
        'ir.attachment', ondelete='set null', readonly=True,
        string="XLSX attachment",
    )
    mail_message_id = fields.Many2one(
        'mail.message', ondelete='set null', readonly=True,
        string="Chatter message",
    )
