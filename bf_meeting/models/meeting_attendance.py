from odoo import fields, models


class MeetingAttendance(models.Model):
    """Présence d'un participant à une réunion."""
    _name = 'meeting.attendance'
    _description = 'Présence à une réunion'
    _order = 'meeting_id, partner_id'

    meeting_id = fields.Many2one(
        'meeting.record',
        string='Réunion',
        required=True,
        ondelete='cascade',
        index=True,
    )
    partner_id = fields.Many2one(
        'res.partner',
        string='Participant',
        required=True,
    )
    status = fields.Selection([
        ('present', 'Présent'),
        ('absent', 'Absent'),
        ('excused', 'Excusé'),
    ], string='Statut', default='present', required=True)
    role = fields.Char(
        string='Rôle',
        help='Ex: animateur, preneur de notes, présentateur',
    )

    _sql_constraints = [
        ('unique_meeting_partner',
         'UNIQUE(meeting_id, partner_id)',
         'Un participant ne peut être enregistré qu\'une seule fois par réunion.'),
    ]
