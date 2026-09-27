"""La durée déclarée doit être celle qui s'applique.

⚠️ **Une politique de conservation qui annonce autre chose que ce que le code
fait est pire que pas de politique.** Elle donne au syndicat une réponse toute
faite à une question qu'un enquêteur posera autrement : ce n'est pas « qu'avez-
vous écrit », c'est « qu'avez-vous gardé ». Le module purge le journal des colis
et des visiteurs selon la durée choisie par CHAQUE syndicat ; le registre des
activités de traitement, lui, porte une seule politique. Les deux peuvent
diverger, et personne ne s'en apercevrait.

Le pont les compare, et le dit à l'écran. Il ne corrige ni l'un ni l'autre :
lequel des deux a raison est une décision du syndicat, pas du logiciel.

⚠️ **Zéro n'est pas une durée courte, c'est l'absence de purge.** Le module
traite `log_retention_days = 0` comme « on ne purge pas », et c'est un choix
légitime pour un syndicat qui gère sa conservation autrement. Mais une finalité
déclarée avec une durée alors que rien ne s'efface est exactement le genre
d'écart que ce champ existe pour montrer.
"""
from odoo import _, api, fields, models


class BfPropertyOrganisation(models.Model):
    _name = "bf.property.organisation"
    _inherit = "bf.property.organisation"

    privacy_retention_declared = fields.Integer(
        string="Durée déclarée au registre des traitements",
        compute="_compute_privacy_retention",
        help="Ce que la politique de conservation du journal des colis et des "
             "visiteurs annonce, au registre des activités de traitement.",
    )
    privacy_retention_warning = fields.Char(
        string="Écart de conservation",
        compute="_compute_privacy_retention",
        help="⚠️ Non stocké : il dépend d'un enregistrement d'un autre module, "
             "que rien n'oblige à passer par ici quand il change.",
    )

    @api.depends("log_retention_days")
    def _compute_privacy_retention(self):
        policy = self.env.ref(
            "bf_property_privacy.retention_property_access_log",
            raise_if_not_found=False,
        )
        declared = policy.sudo().retention_days if policy else 0
        for syndicat in self:
            syndicat.privacy_retention_declared = declared
            applied = syndicat.log_retention_days
            if not policy:
                syndicat.privacy_retention_warning = _(
                    "Aucune politique de conservation n'est déclarée pour le "
                    "journal des colis et des visiteurs."
                )
            elif not applied:
                syndicat.privacy_retention_warning = _(
                    "Le registre des traitements annonce %(declared)d jours, "
                    "et ce syndicat ne purge pas son journal. Ce qui est "
                    "déclaré n'est pas ce qui est fait."
                ) % {"declared": declared}
            elif applied != declared:
                syndicat.privacy_retention_warning = _(
                    "Le registre des traitements annonce %(declared)d jours, "
                    "ce syndicat en applique %(applied)d."
                ) % {"declared": declared, "applied": applied}
            else:
                syndicat.privacy_retention_warning = ""
