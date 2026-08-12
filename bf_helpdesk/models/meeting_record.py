from odoo import fields, models


class MeetingRecord(models.Model):
    _inherit = "meeting.record"

    helpdesk_ticket_id = fields.Many2one(
        comodel_name="helpdesk.ticket",
        string="Ticket d'origine",
        index=True,
        ondelete="set null",
        help="Ticket helpdesk qui a déclenché cette rencontre.",
    )
