from odoo import fields, models


class ResCompany(models.Model):
    _inherit = 'res.company'

    meeting_resend_changes_default = fields.Boolean(
        string="Dire au destinataire ce qui a changé au renvoi",
        default=False,
        help="Valeur par défaut de la case « Dire ce qui a changé depuis le "
             "dernier envoi » sur les nouveaux ordres du jour. Décoché par "
             "défaut : annoncer au client qu'un sujet a été retiré de l'ordre "
             "du jour est un choix, pas une habitude qu'on prend sans le savoir.",
    )
    meeting_logo = fields.Image(
        string="Logo Rencontres",
        help=(
            "Logo affiché sur la bannière sombre des rapports PDF et courriels "
            "du module Rencontres. Utiliser une version monochrome blanche pour "
            "rester lisible. Si vide, le logo standard de la société est utilisé."
        ),
    )
