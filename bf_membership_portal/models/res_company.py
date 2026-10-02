from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    membership_directory = fields.Selection(
        [("closed", "Fermé"),
         ("members", "Membres connectés"),
         ("public", "Public")],
        string="Répertoire des membres", required=True, default="closed",
        help="Fermé d'office (Loi 25, art. 9.1 : les paramètres les plus protecteurs). "
             "Ouvert, il ne montre que les membres en règle qui y ont consenti, et "
             "seulement leur nom et leur ville.",
    )
