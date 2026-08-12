from odoo import fields, models


class GamificationXpTransaction(models.Model):
    _name = 'bf.gamification.xp.transaction'
    _description = 'Transaction XP'
    _order = 'date desc, id desc'

    user_id = fields.Many2one('res.users', string="Utilisateur", required=True,
                              ondelete='cascade', index=True)
    xp_amount = fields.Integer(string="Montant XP", required=True)
    source = fields.Selection([
        ('timesheet', 'Feuille de temps'),
        ('task', 'Tâche'),
        ('document', 'Document'),
        ('badge', 'Badge'),
        ('streak', 'Streak'),
        ('manual', 'Manuel'),
        ('hosting', 'Hébergement'),
        ('reward', 'Récompense'),
        ('message', 'Message'),
        ('helpdesk', 'Ticket'),
        ('activity', 'Activité'),
        ('meeting', 'Compte rendu'),
        ('agenda', 'Ordre du jour'),
        ('decision', 'Décision'),
        ('knowledge_item', 'Élément de matrice'),
        ('resolution', 'Résolution corporative'),
        ('email_triage', 'Triage courriel'),
    ], string="Source", required=True, index=True)
    description = fields.Char(string="Description")
    reference = fields.Reference(
        selection=[
            ('account.analytic.line', 'Feuille de temps'),
            ('project.task', 'Tâche'),
            ('project.document', 'Document'),
            ('knowledge.item', 'Item connaissance'),
            ('bf.gamification.badge', 'Badge'),
            ('bf.gamification.reward.claim', 'Réclamation'),
            ('mail.message', 'Message'),
            ('helpdesk.ticket', 'Ticket'),
            ('meeting.record', 'Compte rendu'),
            ('meeting.agenda', 'Ordre du jour'),
            ('meeting.decision', 'Décision'),
            ('project.knowledge.item', 'Élément de matrice'),
            ('corporate.resolution', 'Résolution corporative'),
            ('bf.email', 'Courriel BF'),
        ],
        string="Référence",
    )
    date = fields.Datetime(string="Date", default=fields.Datetime.now, index=True)
