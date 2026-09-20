# -*- coding: utf-8 -*-
from odoo import api, fields, models
from odoo.exceptions import AccessError


class BabillardJaime(models.Model):
    """Le geste le moins cher qu'une publication puisse demander.

    Sur une nouvelle ou une célébration, il n'y avait rien à faire sauf ouvrir
    la publication. NN/g donne l'abaissement de la barre, une réaction plutôt
    qu'un texte, comme la seule parade documentée à la ville fantôme.

    ⚠️ Comme l'accusé de lecture, cette table ne porte **aucune mesure** : ni
    horodatage de consultation, ni compte de visites, ni pondération. Elle dit
    qu'une personne a aimé une publication, et l'écran n'en rend que le
    CARDINAL. La Loi 25 encadre le profilage, qui inclut l'évaluation du
    rendement au travail.
    """

    _name = "bf.babillard.jaime"
    _description = "J'aime sur une publication du babillard"
    _rec_name = "post_id"

    post_id = fields.Many2one(
        "bf.babillard.post", string="Publication", required=True,
        ondelete="cascade", index=True)
    user_id = fields.Many2one(
        "res.users", string="Personne", required=True, ondelete="cascade",
        index=True, default=lambda s: s.env.user)
    # Portée pour la règle multi-société : un j'aime suit sa publication.
    company_id = fields.Many2one(
        related="post_id.company_id", store=True, string="Société")

    _sql_constraints = [
        ("jaime_unique", "UNIQUE(post_id, user_id)",
         "Une personne n'aime qu'une fois la même publication."),
    ]

    @api.model_create_multi
    def create(self, vals_list):
        # 🔴 Seconde défense, comme sur l'accusé : le bouton passe par
        # `action_basculer_jaime`, qui vérifie l'état et l'audience. Écrire
        # directement dans la table sautait ces gardes, et rien n'empêchait
        # d'aimer au nom de quelqu'un d'autre.
        for vals in vals_list:
            if not self.env.su and vals.get("user_id", self.env.uid) != self.env.uid:
                raise AccessError(self.env._(
                    "On n'aime pas une publication au nom de quelqu'un d'autre."))
        return super().create(vals_list)
