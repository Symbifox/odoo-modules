"""Le type de rendez-vous sait à quelle inscription il appartient.

Le champ vit ICI, dans le satellite, et pas dans `bf_appointment` : un champ
relationnel vers un modèle d'un autre module est une dépendance dure, résolue au
chargement du registre. Posé dans le socle, il rendrait ce module obligatoire
pour tout le monde.
"""

from odoo import fields, models


class ResourceBookingType(models.Model):
    _inherit = "resource.booking.type"

    visit_listing_id = fields.Many2one(
        "bf.visit.listing", string="Inscription à visiter",
        copy=False, ondelete="cascade", index=True,
        help="Rempli par le module de visites. Un type qui porte une "
             "inscription produit une visite à chaque réservation.",
    )
