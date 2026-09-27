"""Le réglage que le pont ne doit pas choisir en silence.

🔴 Sans ce réglage, le pont ne fixait ni le canal du code ni de mot de
passe, donc il héritait des valeurs par défaut du socle : le code par courriel,
sans mot de passe. Le lien et le code arrivaient dans la même boîte, ce qui
ramène la remise à un seul facteur. Ce n'est pas anodin ici : les documents de
l'art. 1068.2 peuvent contenir des renseignements personnels d'AUTRES
copropriétaires, qui n'ont donné aucune autorisation et à qui on ne demandera
rien.

⚠️ **Le numéro consenti aux avis par texto ne sert PAS ici.** La tentation est
de prendre celui de `bf_property_sms` quand il existe. L'art. 1070 al. 1 met ce
renseignement au registre parce que la personne a consenti à être JOINTE POUR
CES AVIS-LÀ ; s'en servir pour autre chose est exactement le glissement que ce
consentement borne. Et de toute façon le destinataire d'une remise est un
tiers — acquéreur, notaire — qui n'a aucune ligne de consentement.

Le canal se règle donc par copropriété, et le texte à l'écran dit ce que chaque
choix implique plutôt que de laisser le réglage par défaut parler à sa place.
"""
from odoo import fields, models


class BfPropertyOrganisation(models.Model):
    _name = "bf.property.organisation"
    _inherit = "bf.property.organisation"

    secure_otp_channel = fields.Selection(
        [
            ("email", "Courriel"),
            ("sms", "Texto au mobile du destinataire"),
        ],
        string="Canal du code de la remise sécurisée",
        default="email",
        required=True,
        help="⚠️ Par courriel, le lien et le code arrivent dans la même boîte : "
             "qui accède à cette boîte accède aux documents. Par texto, il faut "
             "les deux, mais le mobile doit être inscrit à la fiche du "
             "destinataire, sans quoi le socle bascule sur le courriel.",
    )
