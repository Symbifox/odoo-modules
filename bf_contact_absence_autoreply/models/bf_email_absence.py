"""Le message de maison sert aussi à l'agenda.

`bf.email.absence._absence_seed` rend `None` quand la personne n'a pas rédigé
de « message type », et la détection à l'agenda passe alors son tour en
silence. C'est ce préalable qui a produit **zéro enregistrement** partout où
le module tournait.

Ici, on le remplit : à défaut de gabarit personnel, la maison prête le sien.
La règle « sans texte, on ne répond pas » tient toujours, mais il faut
maintenant que les DEUX manquent.
"""

from odoo import api, models


class BfEmailAbsence(models.Model):
    _inherit = "bf.email.absence"

    @api.model
    def _absence_seed(self, user):
        seed = super()._absence_seed(user)
        if seed is not None:
            return seed
        Maison = self.env["bf.absence.house.message"]
        # Dans la langue de la personne : le travail planifié qui appelle ceci
        # n'en porte aucune, et le texte part en son nom.
        maison = Maison._for_tone(Maison._default_tone()).with_context(
            lang=user.lang or "en_US")
        if not maison:
            return None
        # Rien d'autre que le texte : le reste vient des défauts du modèle.
        # Une absence détectée à l'agenda ne refuse aucune invitation et ne
        # confie la boîte à personne : ces deux gestes se demandent.
        return {
            "reply_ids": [(0, 0, {
                "name": maison.name,
                "body_html": maison._rendered_body(),
            })],
        }
