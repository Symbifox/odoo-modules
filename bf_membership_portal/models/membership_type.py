from odoo import fields, models


class MembershipType(models.Model):
    _inherit = "bf.membership.type"

    public_signup = fields.Boolean(
        string="Adhésion en ligne", tracking=True,
        help="La catégorie paraît au formulaire public d'adhésion. Éteint d'office : "
             "l'organisme choisit les catégories ouvertes au public.",
    )
