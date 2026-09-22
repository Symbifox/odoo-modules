# -*- coding: utf-8 -*-
from odoo import api, fields, models
from odoo.exceptions import AccessError


class BabillardGeste(models.Model):
    """Une réaction POSÉE : qui, sur quelle publication, laquelle.

    Le geste le moins cher qu'une publication puisse demander. Sur une nouvelle
    ou une célébration, il n'y avait rien à faire sauf ouvrir la publication.
    NN/g donne l'abaissement de la barre, une réaction plutôt qu'un texte,
    comme la seule parade documentée à la ville fantôme.

    ⚠️ Ce modèle s'appelait `bf.babillard.jaime` jusqu'à la 18.0.1.6.0, quand
    il n'y avait qu'un pouce à poser. Le nom mentait dès la deuxième réaction.

    🔴 Une même personne peut poser PLUSIEURS réactions sur la même
    publication : l'unicité porte sur le triplet, pas sur la paire. Un compteur
    additionne donc des GESTES, pas des personnes, et deux réactions à 5 ne
    disent pas que dix personnes ont réagi.

    ⚠️ Cette table ne porte toujours aucune mesure : ni horodatage de
    consultation, ni compte de visites, ni pondération. Depuis la 18.0.1.6.0,
    l'audience voit QUI a réagi (décision de l'exploitant, 2026-09-19) ; ce qui reste
    hors de portée, c'est l'agrégat par personne. L'accusé de lecture, lui,
    demeure privé : c'est une autre table et un autre usage.
    """

    _name = "bf.babillard.geste"
    _description = "Réaction posée sur une publication du babillard"
    _rec_name = "post_id"

    post_id = fields.Many2one(
        "bf.babillard.post", string="Publication", required=True,
        ondelete="cascade", index=True)
    user_id = fields.Many2one(
        "res.users", string="Personne", required=True, ondelete="cascade",
        index=True, default=lambda s: s.env.user)
    # 🔴 `restrict` et non `cascade` : supprimer une réaction du catalogue
    # effacerait l'historique de toutes les publications qui la portent. Le
    # catalogue refuse déjà la suppression d'une réaction posée, avec un
    # message qui dit quoi faire ; ceci est la garde de la base de données.
    reaction_id = fields.Many2one(
        "bf.babillard.reaction", string="Réaction", required=True,
        ondelete="restrict", index=True)
    # Portée pour la règle multi-société : un geste suit sa publication.
    company_id = fields.Many2one(
        related="post_id.company_id", store=True, string="Société")

    _sql_constraints = [
        ("geste_unique", "UNIQUE(post_id, user_id, reaction_id)",
         "On ne pose pas deux fois la même réaction sur la même publication."),
    ]

    @api.model_create_multi
    def create(self, vals_list):
        # 🔴 Seconde défense, comme sur l'accusé : le bouton passe par
        # `action_basculer_reaction`, qui vérifie l'état, l'audience et que la
        # réaction est bien offerte. Écrire directement dans la table sautait
        # ces gardes, et rien n'empêchait de réagir au nom de quelqu'un
        # d'autre.
        for vals in vals_list:
            if not self.env.su and vals.get("user_id", self.env.uid) != self.env.uid:
                raise AccessError(self.env._(
                    "On ne réagit pas au nom de quelqu'un d'autre."))
        return super().create(vals_list)
