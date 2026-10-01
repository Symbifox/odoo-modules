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
    # Le jour (local à la personne) de la dernière notification
    # « conversations à suivre ». Une seule par jour, même inscrite à plusieurs
    # courriels quotidiens.
    gen_closure_push_date = fields.Date(string="Gen follow-up push", copy=False)
