from odoo import fields, models


class MeetingTopic(models.Model):
    """Sujet abordé lors d'une réunion."""
    _name = 'meeting.topic'
    _description = 'Sujet de réunion'
    _order = 'meeting_id, sequence, id'

    meeting_id = fields.Many2one(
        'meeting.record',
        string='Réunion',
        required=True,
        ondelete='cascade',
        index=True,
    )
    sequence = fields.Integer(
        string='Ordre',
        default=10,
    )
    name = fields.Char(
        string='Sujet',
        required=True,
    )
    points_html = fields.Html(
        string='Points clés',
        sanitize_style=True,
    )
