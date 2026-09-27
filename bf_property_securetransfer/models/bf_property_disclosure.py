"""Art. 1068.2 : les documents au promettant acheteur.

⚠️ Aucun délai chiffré au texte : « avec diligence ». Le module compte les jours
sans déclarer de retard, et le pont ne change rien à ça.

🔴 **La revue de vie privée se fait AVANT la remise.** L'autorisation du
promettant acheteur au sens de l'art. 37 ne couvre pas les renseignements
personnels des AUTRES copropriétaires. Une fois la pièce partie par un lien
sécurisé, elle est partie : le caviardage ne se rattrape pas.
"""
from odoo import _, models
from odoo.exceptions import UserError


class BfPropertyDisclosure(models.Model):
    _name = "bf.property.disclosure"
    _inherit = ["bf.property.disclosure", "bf.property.secure.delivery"]

    # ⚠️ Rien à geler : les pièces remises sont celles que le syndicat a
    # jointes à la fiche. Elles y sont déjà, et les dupliquer doublerait la
    # liste des pièces sans rien ajouter à la preuve.
    _secure_freezes_copy = False

    def _secure_recipient(self):
        return self.requester_partner_id

    def _secure_subject(self):
        return _("Documents de copropriété : %s") % self.organisation_id.name

    def _secure_documents(self):
        """Ce régime transmet des pièces, il n'en produit aucune."""
        return self._secure_joined_documents()

    def _secure_check_ready(self):
        if self.state == "cancelled":
            raise UserError(_("« %s » est annulée.") % self.name)
        if self.state == "requested":
            raise UserError(
                _(
                    "La revue de vie privée n'est pas faite. L'art. 1068.2 "
                    "n'autorise pas la communication des renseignements "
                    "personnels des autres copropriétaires, et une pièce partie "
                    "par un lien sécurisé ne se caviarde plus."
                )
            )
        return True
