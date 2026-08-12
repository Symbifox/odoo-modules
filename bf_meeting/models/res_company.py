from odoo import fields, models


class ResCompany(models.Model):
    _inherit = 'res.company'

    meeting_logo = fields.Image(
        string="Logo Rencontres",
        help=(
            "Logo affiché sur la bannière sombre des rapports PDF et courriels "
            "du module Rencontres. Utiliser une version monochrome blanche pour "
            "rester lisible. Si vide, le logo standard de la société est utilisé."
        ),
    )
