from odoo import fields, models


class ResCompany(models.Model):
    _inherit = 'res.company'

    meeting_exchange_default = fields.Boolean(
        string='Joindre la copie machine aux comptes rendus',
        default=False,
        help="Valeur par défaut de la case « Joindre la copie lisible par la "
             "machine » sur les nouveaux comptes rendus. Décoché par défaut : "
             "joindre une copie reprenable est un choix, pas une habitude "
             "qu'on prend sans le savoir.",
    )
