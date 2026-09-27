"""Ce qu'un dépôt au portail envoie par COURRIEL à une personne du bureau.

🔴 Le concierge d'Exploitation reçoit ses
notifications dans Odoo (le groupe impose « boîte Odoo »). Une
demande « ⚠️ Sécurité » — une odeur de gaz — y attendait qu'il ouvre Odoo.

Par défaut, les demandes de sécurité partent AUSSI par
courriel ; chaque personne règle ce choix dans ses préférences. Le réglage ne
touche que les comptes « boîte Odoo » : les autres reçoivent déjà le courriel
par la notification ordinaire.
"""
from odoo import fields, models

PORTAL_EMAIL_CHOICES = [
    ("safety", "Demandes de sécurité seulement"),
    ("all", "Tous les dépôts du portail"),
    ("none", "Aucun courriel"),
]


class ResUsers(models.Model):
    _inherit = "res.users"

    bf_property_portal_email = fields.Selection(
        PORTAL_EMAIL_CHOICES,
        string="Dépôts du portail par courriel",
        default="safety",
        help="Pour les comptes qui reçoivent leurs notifications dans Odoo : ce "
             "qui part AUSSI par courriel quand un résident dépose une demande, "
             "une réservation ou un visiteur au portail.",
    )

    @property
    def SELF_READABLE_FIELDS(self):
        return super().SELF_READABLE_FIELDS + ["bf_property_portal_email"]

    @property
    def SELF_WRITEABLE_FIELDS(self):
        return super().SELF_WRITEABLE_FIELDS + ["bf_property_portal_email"]
