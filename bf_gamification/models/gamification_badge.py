import logging

from odoo import api, fields, models
from odoo.tools.safe_eval import safe_eval

_logger = logging.getLogger(__name__)

# Models allowed for automatic badge condition evaluation (sudo search_count)
ALLOWED_CONDITION_MODELS = {
    'account.analytic.line',
    'project.document',
    'mail.message',
    'project.task',
    'helpdesk.ticket',
    'hosting.maintenance.schedule',
}


class GamificationBadge(models.Model):
    _name = 'bf.gamification.badge'
    _description = 'Badge de gamification'
    _order = 'category_id, name'

    name = fields.Char(string="Nom", required=True)
    description = fields.Text(string="Description")
    image = fields.Image(string="Image", max_width=256, max_height=256)
    image_locked = fields.Image(string="Image verrouillée", max_width=256, max_height=256)
    category_id = fields.Many2one('bf.gamification.badge.category', string="Catégorie")
    xp_reward = fields.Integer(string="Récompense XP", default=0)
    rarity = fields.Selection([
        ('common', 'Commun'),
        ('uncommon', 'Peu commun'),
        ('rare', 'Rare'),
        ('epic', 'Épique'),
        ('legendary', 'Légendaire'),
    ], string="Rareté", default='common', required=True)
    condition_type = fields.Selection([
        ('manual', 'Manuel'),
        ('automatic', 'Automatique'),
        ('threshold', 'Seuil'),
    ], string="Type de condition", default='manual', required=True)
    condition_model = fields.Char(string="Modèle surveillé")
    condition_domain = fields.Char(string="Domaine de filtrage")
    condition_threshold = fields.Integer(string="Seuil")
    condition_user_field = fields.Char(
        string="Champ utilisateur", default='create_uid',
        help="Champ du modèle surveillé lié à l'utilisateur (ex: create_uid, user_id, user_ids)")
    threshold_field = fields.Selection([
        ('total_xp', 'XP total'),
        ('longest_streak', 'Meilleur streak'),
        ('current_streak', 'Streak actuel'),
    ], string="Champ de seuil", default='total_xp',
        help="Champ du profil à comparer au seuil (badges de type seuil)")
    unique = fields.Boolean(string="Unique", default=True,
                            help="Ne peut être obtenu qu'une seule fois")
    active = fields.Boolean(string="Actif", default=True)
    hidden = fields.Boolean(string="Caché", default=False,
                            help="Nom et description masqués tant que non obtenu")
    popup_effect = fields.Selection([
        ('none', 'Aucun'),
        ('confetti', 'Confettis'),
        ('fireworks', 'Feux d\'artifice'),
        ('glow', 'Halo lumineux'),
        ('shake', 'Secousse'),
    ], string="Effet visuel", default='confetti')
    popup_sound = fields.Selection([
        ('none', 'Aucun'),
        ('achievement', 'Achievement'),
        ('levelup', 'Level Up'),
        ('fanfare', 'Fanfare'),
    ], string="Son", default='achievement')
    popup_message = fields.Char(string="Message popup")

    @api.constrains('condition_model')
    def _check_condition_model(self):
        for badge in self:
            if badge.condition_model and badge.condition_model not in ALLOWED_CONDITION_MODELS:
                raise models.ValidationError(
                    "Le modele '%s' n'est pas autorise pour les conditions de badge. "
                    "Modeles permis : %s" % (
                        badge.condition_model,
                        ', '.join(sorted(ALLOWED_CONDITION_MODELS)),
                    )
                )

    earned_count = fields.Integer(string="Nombre d'obtentions", compute='_compute_earned_count')
    earned_percentage = fields.Float(string="Rareté (%)", compute='_compute_earned_percentage')

    def _compute_earned_count(self):
        UserBadge = self.env['bf.gamification.user.badge']
        for badge in self:
            badge.earned_count = UserBadge.search_count([('badge_id', '=', badge.id)])

    def _compute_earned_percentage(self):
        total_profiles = self.env['bf.gamification.profile'].search_count([])
        UserBadge = self.env['bf.gamification.user.badge']
        for badge in self:
            if total_profiles:
                # Count distinct users who earned this badge
                earned = len(UserBadge.read_group(
                    [('badge_id', '=', badge.id)],
                    ['user_id'], ['user_id'],
                ))
                badge.earned_percentage = round(earned / total_profiles * 100, 1)
            else:
                badge.earned_percentage = 0.0

    def _get_user_progress(self, user):
        """Return {current, target, percent} for a badge and a user.
        - automatic: search_count(domain + create_uid) / condition_threshold
        - threshold: total_xp / condition_threshold
        - manual: {current: 0 or 1, target: 1} (binary)
        """
        self.ensure_one()
        UserBadge = self.env['bf.gamification.user.badge']
        earned = UserBadge.search_count([
            ('user_id', '=', user.id),
            ('badge_id', '=', self.id),
        ]) > 0

        if self.condition_type == 'threshold' and self.condition_threshold:
            profile = self.env['bf.gamification.profile'].search(
                [('user_id', '=', user.id)], limit=1)
            check_field = self.threshold_field or 'total_xp'
            current = getattr(profile, check_field, 0) if profile else 0
            target = self.condition_threshold
            return {
                'current': min(current, target),
                'target': target,
                'percent': min(round(current / target * 100, 1), 100) if target else 100,
                'earned': earned,
            }
        elif (self.condition_type == 'automatic' and self.condition_model
              and self.condition_domain
              and self.condition_model in ALLOWED_CONDITION_MODELS):
            try:
                domain = safe_eval(self.condition_domain, {"uid": user.id}, mode="eval", nocopy=True)
                if not isinstance(domain, list):
                    raise ValueError("Domain must be a list")
                user_field = self.condition_user_field or 'create_uid'
                domain.append((user_field, '=', user.id))
                count = self.env[self.condition_model].sudo().search_count(domain)
                target = self.condition_threshold or 1
                return {
                    'current': min(count, target),
                    'target': target,
                    'percent': min(round(count / target * 100, 1), 100) if target else 100,
                    'earned': earned,
                }
            except Exception:
                _logger.warning(
                    "Badge %s: invalid condition_domain: %s",
                    self.name, self.condition_domain,
                )
                pass
        # manual or fallback
        return {
            'current': 1 if earned else 0,
            'target': 1,
            'percent': 100.0 if earned else 0.0,
            'earned': earned,
        }
