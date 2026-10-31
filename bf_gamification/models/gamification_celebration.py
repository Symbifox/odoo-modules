"""Une fête de Fox Quest ne se montre qu'une fois.

Le bus d'Odoo porte chaque message à TOUS les onglets ouverts de la personne. Le
renard « à jour » s'affichait donc dans chacun ; dans un onglet en arrière-plan,
son animation de sortie ne tourne pas, et il attendait que la personne y revienne.
Résultat : le même renard à chaque onglet rouvert.

Chaque fête (renard « à jour », carte de défis complète, badge, niveau) part
maintenant avec un jeton à usage unique. L'onglet que la personne regarde le
réclame ; seul le premier à le réclamer montre la fête, les autres se taisent.
Le serveur tranche, donc la règle vaut aussi d'une fenêtre, d'un navigateur ou
d'un appareil à l'autre.
"""
import secrets
from datetime import timedelta

from odoo import api, fields, models

# Une fête qu'aucun onglet n'a réclamée dans ce délai ne sort plus : la personne
# était ailleurs, et un renard en retard n'a plus de sens.
CLAIM_VALIDITY = timedelta(minutes=10)
# Les jetons ne servent à rien après leur délai ; on garde un mois pour le diagnostic.
RETENTION = timedelta(days=30)
CELEBRATION_KINDS = [
    ('caught_up', 'À jour'),
    ('challenge_card', 'Carte de défis complète'),
    ('badge', 'Badge'),
    ('level_up', 'Niveau supérieur'),
]


class GamificationCelebration(models.Model):
    _name = 'bf.gamification.celebration'
    _description = 'Fête Fox Quest à montrer une seule fois'
    _order = 'id desc'

    user_id = fields.Many2one('res.users', string="Utilisateur", required=True,
                              ondelete='cascade', index=True)
    token = fields.Char(string="Jeton", required=True, copy=False)
    kind = fields.Selection(CELEBRATION_KINDS, string="Fête", required=True)
    claimed_at = fields.Datetime(string="Montrée le", readonly=True)

    _sql_constraints = [
        ('token_unique', 'UNIQUE(token)', "Un jeton de fête ne sert qu'une fois."),
    ]

    @api.model
    def _bf_issue(self, user, kind):
        """Crée le jeton d'une fête pour `user` et le rend, à mettre dans le message du bus."""
        token = secrets.token_urlsafe(18)
        self.sudo().create({'user_id': user.id, 'token': token, 'kind': kind})
        return token

    @api.model
    def _bf_claim(self, token):
        """Vrai pour le PREMIER onglet de la personne connectée qui réclame ce jeton.

        Un seul UPDATE conditionnel : deux onglets qui réclament en même temps
        butent sur la même ligne, le second échoue en sérialisation et Odoo rejoue
        sa requête, qui lit alors la fête déjà montrée. Le jeton d'une autre
        personne, inconnu ou périmé ne rend rien et ne consomme rien, pas plus
        qu'une session d'usurpation du support.
        """
        if not token or not isinstance(token, str):
            return False
        # Le support qui voit Odoo comme la personne (`bf_impersonate`) reçoit ses
        # messages de bus : son onglet ne doit pas lui prendre sa fête.
        if self.env['bf.gamification.profile']._bf_is_impersonated():
            return False
        self.env.cr.execute("""
            UPDATE bf_gamification_celebration
               SET claimed_at = (now() AT TIME ZONE 'UTC')
             WHERE token = %s
               AND user_id = %s
               AND claimed_at IS NULL
               AND create_date > (now() AT TIME ZONE 'UTC') - %s
         RETURNING id
        """, (token, self.env.uid, CLAIM_VALIDITY))
        claimed = bool(self.env.cr.fetchone())
        if claimed:
            self.env['bf.gamification.celebration'].invalidate_model(['claimed_at'])
        return claimed

    @api.autovacuum
    def _gc_celebrations(self):
        self.sudo().search([
            ('create_date', '<', fields.Datetime.now() - RETENTION),
        ]).unlink()
