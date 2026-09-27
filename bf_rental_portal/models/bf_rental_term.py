"""Ce qui reste dû, et ce que le portail refuse d'additionner.

🔴 **Aucun total du bail.** L'art. 1905 C.c.Q. rend sans effet la clause qui
rendrait exigible le loyer entier en cas de défaut. Afficher « vous devez
14 400 $ » sur un bail de douze mois réclamerait par l'écran ce qu'on ne peut
pas réclamer en droit, et personne n'aurait écrit la clause : il aurait suffi
d'additionner tous les termes.

⚠️ **Et « déposé au greffe » n'est pas un impayé.** L'art. 1907 permet au
locataire de déposer son loyer au tribunal, sur préavis de dix jours et
autorisation. Il a payé, ailleurs. Le compter comme un arrérage accuserait
quelqu'un d'avoir fait exactement ce que la loi lui permet.
"""
from odoo import api, models

# Les deux seuls états qui portent une dette échue. `pending` est à venir,
# `paid` est éteint, `deposited` est payé ailleurs.
OWED_STATES = ("late", "partial")


class BfRentalTerm(models.Model):
    _inherit = "bf.rental.term"

    @api.model
    def _portal_outstanding(self, terms):
        """Ce qui reste dû sur les termes DÉJÀ EXIGIBLES, et rien d'autre."""
        return sum(
            terms.filtered(lambda t: t.state in OWED_STATES)
            .mapped("amount_outstanding")
        )
