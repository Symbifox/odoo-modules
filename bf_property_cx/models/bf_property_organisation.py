"""L'interrupteur de la mesure, au syndicat, et à l'arrêt.

⚠️ **Un seul interrupteur ici, et c'est voulu.** Le pont texto en porte deux,
parce qu'un numéro de téléphone n'est pas au registre de droit : l'art. 1070
al. 1 C.c.Q. y met le nom et l'adresse, et n'y met les autres renseignements
personnels qu'avec le consentement exprès de la personne. Rien de tel ici : le
module n'inscrit aucune adresse de courriel au registre, il écrit à la personne
qui vient d'écrire au syndicat, à l'adresse par laquelle elle l'a fait, au sujet
de sa propre demande.

Ce qui reste vrai, c'est qu'une demande d'avis est une sollicitation. Elle a
donc deux freins qui ne viennent pas d'ici : le garde-fou anti-sursollicitation
de bf_cx, qui compte par personne et pas par syndicat, et le lien de
désabonnement que porte chaque courriel. L'interrupteur du syndicat est le
troisième, et il passe avant les deux autres.
"""
from odoo import fields, models


class BfPropertyOrganisation(models.Model):
    _inherit = "bf.property.organisation"

    cx_feedback_enabled = fields.Boolean(
        string="Mesurer la satisfaction des occupants",
        default=False,
        help="⚠️ À l'arrêt par défaut. Une fois ouvert, une demande "
             "d'entretien RÉGLÉE fait partir à son demandeur une demande "
             "d'avis en trois émojis. Une demande refusée n'en fait jamais "
             "partir : refuser, c'est dire que la demande sort de l'objet du "
             "syndicat (art. 1039 C.c.Q.), et mesurer la satisfaction juste "
             "après mesurerait le refus.",
    )

    def _cx_feedback_open(self):
        """La mesure est-elle ouverte pour ce syndicat ?

        Une méthode plutôt qu'une lecture directe : le jour où une deuxième
        condition s'ajoute, elle a un endroit où vivre, et les appelants n'ont
        pas à la connaître.
        """
        self.ensure_one()
        return bool(self.cx_feedback_enabled)
