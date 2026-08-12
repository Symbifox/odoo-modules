from odoo import api, fields, models
from odoo.exceptions import ValidationError


class MeetingDecision(models.Model):
    """Décision prise lors d'une réunion."""
    _name = 'meeting.decision'
    _description = 'Décision de réunion'
    _order = 'meeting_id, sequence, id'

    meeting_id = fields.Many2one(
        'meeting.record',
        string='Réunion',
        ondelete='cascade',
        index=True,
    )
    agenda_id = fields.Many2one(
        'meeting.agenda',
        string='Ordre du jour',
        ondelete='cascade',
        index=True,
        help="Décision capturée pendant la rencontre (avant la création du "
             "compte rendu). Sera transférée vers meeting_id à la création "
             "du compte rendu.",
    )
    sequence = fields.Integer(
        string='Ordre',
        default=10,
    )
    name = fields.Char(
        string='Décision',
        required=True,
    )
    description = fields.Html(
        string='Contexte',
        sanitize_style=True,
    )
    decision_maker_id = fields.Many2one(
        'res.partner',
        string='Décideur',
    )
    knowledge_item_id = fields.Many2one(
        'project.knowledge.item',
        string='Élément de matrice',
    )

    @api.constrains('meeting_id', 'agenda_id')
    def _check_parent(self):
        for rec in self:
            if not rec.meeting_id and not rec.agenda_id:
                raise ValidationError(
                    "Une décision doit être rattachée soit à un compte rendu, "
                    "soit à un ordre du jour."
                )
