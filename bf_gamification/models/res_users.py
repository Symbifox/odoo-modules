from odoo import fields, models


class ResUsers(models.Model):
    _inherit = 'res.users'

    # Un robot d'intégration (le processeur de rencontres, par exemple)
    # pouvait finir en tête du classement et recevoir les crédits « CR envoyé ».
    # Un compte coché ne gagne plus rien et sort du classement ; son
    # historique reste.
    bf_gamification_excluded = fields.Boolean(
        string="Hors Fox Quest",
        help="Compte de service ou d'intégration : il ne gagne plus d'XP, de "
             "badge ni de jour de congé, et n'apparaît plus au classement. "
             "Son historique est gardé.",
    )
