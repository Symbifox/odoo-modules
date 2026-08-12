from odoo import fields, models


class ResPartner(models.Model):
    """Extension de res.partner pour le tableau de bord rencontres."""
    _inherit = 'res.partner'

    bf_skip_dashboard = fields.Boolean(
        string='Exclure du tableau de bord rencontres',
        help="Cocher pour masquer toutes les rencontres rattachées à ce "
             "contact du tableau de bord des rencontres (OdJ et CR).",
    )
