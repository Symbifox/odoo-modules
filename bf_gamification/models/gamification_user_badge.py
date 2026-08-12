from odoo import fields, models


class GamificationUserBadge(models.Model):
    _name = 'bf.gamification.user.badge'
    _description = 'Badge obtenu'
    _order = 'date_earned desc'

    user_id = fields.Many2one('res.users', string="Utilisateur", required=True, ondelete='cascade')
    badge_id = fields.Many2one('bf.gamification.badge', string="Badge", required=True, ondelete='cascade')
    date_earned = fields.Datetime(string="Date d'obtention", default=fields.Datetime.now)
    granted_by = fields.Many2one('res.users', string="Accordé par")
    note = fields.Text(string="Note")

    # No SQL unique constraint — badges with unique=False can be earned multiple times.
    # Uniqueness is enforced in _grant_badge() when badge.unique is True.
