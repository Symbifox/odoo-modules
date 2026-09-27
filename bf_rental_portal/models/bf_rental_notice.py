"""Ce qui appelle une réponse, et ce qui n'en appelle plus.

⚠️ **Cette sélection ne peut PAS vivre dans une `ir.rule`.** Le domaine d'une
règle est mis en cache par `ormcache` sans composante temporelle : une date y
est évaluée une fois puis gelée, et « il vous reste cinq jours » resterait
affiché des mois. Elle ne vit pas non plus au contrôleur, où elle ne serait
éprouvable qu'en cherchant des chaînes dans une page HTTP. Elle vit ici, où un
test la prend de face.
"""
from odoo import api, fields, models


class BfRentalNotice(models.Model):
    _inherit = "bf.rental.notice"

    @api.model
    def _portal_pending(self, notices):
        """Les avis dont le délai de réponse court ENCORE.

        🔴 Trois conditions, et chacune retire quelque chose de faux.

        1. `state == "given"` : un avis accepté ou refusé n'appelle plus rien.
        2. Une échéance connue. Elle se calcule sur la RÉCEPTION, et le module
           n'en invente pas : sans date de réception, il n'y a pas d'échéance.
        3. 🔴 L'échéance n'est pas passée. Sans elle, l'écran dirait « vous avez
           jusqu'au 3 mars » un 12 septembre. Pire qu'inutile : un compteur qui
           ne se vide jamais cesse d'être lu, et le jour où il porte quelque
           chose de vrai, personne ne le regarde plus.
        """
        today = fields.Date.context_today(self.env.user)
        return notices.filtered(
            lambda n: n.state == "given"
            and n.response_deadline
            and n.response_deadline >= today
        )
