# -*- coding: utf-8 -*-
from odoo import models


class HrEmployee(models.Model):
    """Le ménage que la base ne fera pas à notre place.

    🔴 `bf.babillard.post.personne_id` pointe `hr.employee.public`, qui est une
    VUE SQL. Odoo ne crée aucune clé étrangère vers une vue, donc un
    `ondelete="set null"` déclaré sur le champ ne s'exécute JAMAIS : il a l'air
    d'une protection et n'en est pas une.

    Sans ce crochet, un employé supprimé (pas archivé : supprimé) laisse un
    identifiant mort dans la publication. Le champ lié `personne_nom` lève alors
    MissingError à la lecture, et comme le fil lit ce champ sur chaque carte,
    c'est le babillard ENTIER qui tombe, pour toute l'audience, pas seulement la
    carte fautive.
    """

    _inherit = "hr.employee"

    def unlink(self):
        if self.ids:
            self.env["bf.babillard.post"].sudo().search(
                [("personne_id", "in", self.ids)]).write({"personne_id": False})
        return super().unlink()
