from odoo import fields, models


class ResUsers(models.Model):
    _inherit = "res.users"

    # Ce que la liste des conversations de Gen affiche, le titre
    # ou l'élément associé. Sur l'usager et non sur l'appareil, pour que le
    # web et le mobile suivent le même choix.
    gen_list_mode = fields.Selection(
        [("title", "Titre"), ("element", "Élément associé")],
        string="Liste Gen",
        default="title",
    )
