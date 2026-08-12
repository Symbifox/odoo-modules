from odoo import api, fields, models
from odoo.exceptions import UserError


class GamificationReward(models.Model):
    _name = 'bf.gamification.reward'
    _description = 'Récompense Fox Quest'
    _order = 'xp_cost asc'

    name = fields.Char(string="Nom", required=True)
    description = fields.Html(string="Description")
    image = fields.Image(string="Image", max_width=256, max_height=256)
    xp_cost = fields.Integer(string="Coût XP", required=True)
    available = fields.Boolean(string="Disponible", default=True)
    stock = fields.Integer(string="Stock", default=0,
                           help="0 = illimité")
    limit_per_user = fields.Integer(string="Limite par utilisateur", default=0,
                                     help="0 = illimité")
    category = fields.Selection([
        ('time_off', 'Congé'),
        ('perk', 'Avantage'),
        ('recognition', 'Reconnaissance'),
        ('custom', 'Personnalisé'),
    ], string="Catégorie", default='perk', required=True)
    active = fields.Boolean(string="Actif", default=True)
    claim_count = fields.Integer(string="Réclamations", compute='_compute_claim_count')

    def _compute_claim_count(self):
        Claim = self.env['bf.gamification.reward.claim']
        for reward in self:
            reward.claim_count = Claim.search_count([
                ('reward_id', '=', reward.id),
                ('state', '!=', 'refused'),
            ])


class GamificationRewardClaim(models.Model):
    _name = 'bf.gamification.reward.claim'
    _description = 'Réclamation de récompense'
    _order = 'date_claimed desc'
    _inherit = ['mail.thread']

    user_id = fields.Many2one('res.users', string="Utilisateur", required=True,
                              ondelete='cascade', default=lambda self: self.env.user)
    reward_id = fields.Many2one('bf.gamification.reward', string="Récompense",
                                required=True, ondelete='restrict')
    state = fields.Selection([
        ('pending', 'En attente'),
        ('approved', 'Approuvée'),
        ('refused', 'Refusée'),
        ('consumed', 'Consommée'),
    ], string="État", default='pending', required=True, tracking=True)
    xp_spent = fields.Integer(string="XP dépensé")
    date_claimed = fields.Datetime(string="Date de réclamation",
                                    default=fields.Datetime.now)
    date_processed = fields.Datetime(string="Date de traitement")
    approved_by = fields.Many2one('res.users', string="Approuvé par")
    refusal_reason = fields.Text(string="Raison du refus")

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            reward = self.env['bf.gamification.reward'].browse(vals.get('reward_id'))
            user = self.env['res.users'].browse(vals.get('user_id', self.env.user.id))

            # Check availability
            if not reward.available:
                raise UserError("Cette récompense n'est pas disponible.")

            # Check stock
            if reward.stock > 0:
                claimed = self.search_count([
                    ('reward_id', '=', reward.id),
                    ('state', 'in', ['pending', 'approved', 'consumed']),
                ])
                if claimed >= reward.stock:
                    raise UserError("Stock épuisé pour cette récompense.")

            # Check per-user limit
            if reward.limit_per_user > 0:
                user_claims = self.search_count([
                    ('reward_id', '=', reward.id),
                    ('user_id', '=', user.id),
                    ('state', 'in', ['pending', 'approved', 'consumed']),
                ])
                if user_claims >= reward.limit_per_user:
                    raise UserError("Limite de réclamations atteinte pour cette récompense.")

            # Check XP balance
            profile = self.env['bf.gamification.profile']._get_or_create_profile(user)
            if profile.total_xp < reward.xp_cost:
                raise UserError(
                    "XP insuffisant. Vous avez %d XP, il en faut %d." % (
                        profile.total_xp, reward.xp_cost))

            vals['xp_spent'] = reward.xp_cost

        claims = super().create(vals_list)

        # Deduct XP for each claim
        for claim in claims:
            profile = self.env['bf.gamification.profile']._get_or_create_profile(claim.user_id)
            self.env['bf.gamification.xp.transaction'].sudo().create({
                'user_id': claim.user_id.id,
                'xp_amount': -claim.xp_spent,
                'source': 'reward',
                'description': 'Récompense : %s' % claim.reward_id.name,
                'reference': 'bf.gamification.reward.claim,%s' % claim.id,
            })

        return claims

    def action_approve(self):
        self.write({
            'state': 'approved',
            'date_processed': fields.Datetime.now(),
            'approved_by': self.env.user.id,
        })

    def action_refuse(self):
        for claim in self:
            claim.state = 'refused'
            claim.date_processed = fields.Datetime.now()
            # Refund XP
            self.env['bf.gamification.xp.transaction'].sudo().create({
                'user_id': claim.user_id.id,
                'xp_amount': claim.xp_spent,
                'source': 'reward',
                'description': 'Remboursement : %s' % claim.reward_id.name,
                'reference': 'bf.gamification.reward.claim,%s' % claim.id,
            })

    def action_consume(self):
        self.filtered(lambda c: c.state == 'approved').write({'state': 'consumed'})
