# -*- coding: utf-8 -*-
from odoo import api, fields, models
from odoo.exceptions import AccessError


class BabillardVote(models.Model):
    """Une voix posée : qui, sur quelle publication, pour quel choix.

    Le jumeau de `bf.babillard.geste`, et volontairement de la même forme :
    l'unicité porte sur le triplet, ce qui donne le choix multiple sans une
    ligne de plus. Ce qui change, c'est qu'un vote peut être ANONYME, et
    l'anonymat ne se décrète pas à l'écran.

    🔴 Trois endroits le tiennent, et il faut les trois :

    * la table n'est ni créée ni modifiée par l'audience (droits en lecture
      seule), tout passe par `action_basculer_vote` qui revérifie ;
    * la règle d'enregistrement ne rend les voix d'un sondage anonyme à
      PERSONNE, rédaction comprise, sauf les siennes ;
    * le dépouillement ne sort que par le champ calculé de la publication, qui
      applique le seuil sous lequel un résultat anonyme se déchiffre par
      soustraction.

    Un compteur posé sur l'option aurait ouvert une quatrième porte : il n'y en
    a pas.
    """

    _name = "bf.babillard.vote"
    _description = "Voix posée sur un sondage du babillard"
    _rec_name = "option_id"

    post_id = fields.Many2one(
        "bf.babillard.post", string="Publication", required=True,
        ondelete="cascade", index=True)
    option_id = fields.Many2one(
        "bf.babillard.option", string="Choix", required=True,
        ondelete="cascade", index=True)
    user_id = fields.Many2one(
        "res.users", string="Personne", required=True, ondelete="cascade",
        index=True, default=lambda s: s.env.user)
    company_id = fields.Many2one(
        related="post_id.company_id", store=True, string="Société")

    _sql_constraints = [
        ("vote_unique", "UNIQUE(post_id, user_id, option_id)",
         "On ne vote pas deux fois pour le même choix."),
    ]

    @api.model_create_multi
    def create(self, vals_list):
        # 🔴 Seconde défense, comme sur la réaction : le bouton passe par
        # `action_basculer_vote`, qui vérifie l'état du sondage, l'audience et
        # que le choix appartient bien à cette publication.
        for vals in vals_list:
            if not self.env.su and vals.get("user_id", self.env.uid) != self.env.uid:
                raise AccessError(self.env._("On ne vote pas au nom de quelqu'un d'autre."))
        return super().create(vals_list)

    @api.constrains("post_id", "option_id")
    def _check_option_de_la_publication(self):
        """🔴 Le choix qui arrive du navigateur ne vaut rien tant qu'il n'a pas
        été confronté à la publication : sans cette garde, un identifiant
        d'option emprunté à un AUTRE sondage se comptait ici."""
        for vote in self:
            if vote.option_id.post_id != vote.post_id:
                raise AccessError(self.env._(
                    "Ce choix n'appartient pas à cette publication."))
