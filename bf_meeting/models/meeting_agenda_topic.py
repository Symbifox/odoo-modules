from odoo import fields, models


class MeetingAgendaTopic(models.Model):
    """Sujet planifié dans un ordre du jour."""
    _name = 'meeting.agenda.topic'
    _description = "Sujet d'ordre du jour"
    _order = 'agenda_id, sequence, id'

    agenda_id = fields.Many2one(
        'meeting.agenda',
        string='Ordre du jour',
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
    duration_planned = fields.Integer(
        string='Durée (min)',
        help='Temps alloué pour ce sujet en minutes.',
    )
    presenter_id = fields.Many2one(
        'res.partner',
        string='Intervenant',
    )
    description = fields.Html(
        string='Notes / Contexte',
        sanitize_style=True,
    )
    live_notes_html = fields.Html(
        string='Notes (rencontre)',
        sanitize_style=True,
        help="Prise de notes en direct pendant la rencontre. N'apparaît pas "
             "dans le PDF de l'ordre du jour ; sera transféré dans le compte "
             "rendu à sa création.",
    )

    # Provenance & moderation (public contributions)
    source = fields.Selection(
        [
            ('staff', 'Interne'),
            ('contributed', 'Proposé par un destinataire'),
        ],
        string='Origine',
        default='staff',
        required=True,
        index=True,
        help="« Proposé par un destinataire » : sujet soumis via le lien "
             "public de contribution, en attente de modération.",
    )
    contributor_name = fields.Char(string='Proposé par')
    contributor_email = fields.Char(string='Courriel du contributeur')
    moderation_state = fields.Selection(
        [
            ('pending', 'À examiner'),
            ('accepted', 'Accepté'),
            ('rejected', 'Rejeté'),
        ],
        string='Modération',
        default='accepted',
        required=True,
        help="Les sujets internes sont acceptés par défaut. Les sujets "
             "proposés par un destinataire restent « À examiner » tant que "
             "le gestionnaire ne les a pas acceptés : ils n'apparaissent ni "
             "dans le PDF ni dans le courriel d'ordre du jour.",
    )

    def action_accept_contribution(self):
        """Accepter un sujet proposé : il rejoint l'ordre du jour officiel."""
        self.write({'moderation_state': 'accepted'})

    def action_reject_contribution(self):
        """Rejeter un sujet proposé : il reste consultable mais hors OdJ."""
        self.write({'moderation_state': 'rejected'})
