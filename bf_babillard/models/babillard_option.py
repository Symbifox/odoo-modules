# -*- coding: utf-8 -*-
from odoo import api, fields, models
from odoo.exceptions import ValidationError


class BabillardOption(models.Model):
    """Un choix offert par un sondage du fil.

    Une option appartient à la PUBLICATION, pas à la maison. C'est toute la
    différence avec une réaction : le catalogue des réactions est configuré une
    fois pour la société et se repose sur chaque carte, alors qu'un sondage
    apporte ses propres choix et les emporte avec lui.

    🔴 `propose_par_id` porte un `groups=` : sur un sondage anonyme, savoir qui
    a proposé « le jeudi » en dit déjà long sur qui votera « le jeudi ». Le
    champ reste en base pour que le plafond par personne soit applicable et
    pour que la rédaction puisse répondre d'un contenu déplacé ; il ne sort pas
    par RPC pour le reste de l'audience. Ce que l'écran affiche passe par le
    champ calculé `sondage` de la publication, qui n'y met le nom que sur un
    sondage nominatif.
    """

    _name = "bf.babillard.option"
    _description = "Option offered by a noticeboard poll"
    _order = "post_id, sequence, id"

    post_id = fields.Many2one(
        "bf.babillard.post", string="Post", required=True,
        ondelete="cascade", index=True)
    name = fields.Char("Options", required=True, translate=False)
    sequence = fields.Integer("Sequence", default=10)
    propose_par_id = fields.Many2one(
        "res.users", string="Suggested by", readonly=True, ondelete="set null",
        groups="bf_babillard.group_babillard_redacteur",
        help="Empty when the option comes from the editors with the post. "
             "Filled in when somebody in the audience added it.")
    # Portée pour la règle multi-société : une option suit sa publication.
    company_id = fields.Many2one(
        related="post_id.company_id", store=True, string="Company")
    vote_ids = fields.One2many(
        "bf.babillard.vote", "option_id", string="Votes")

    _sql_constraints = [
        ("libelle_non_vide", "CHECK (length(trim(name)) > 0)",
         "An option with no text is not an option."),
    ]

    @api.constrains("post_id")
    def _check_publication_est_un_sondage(self):
        """Une option ne s'accroche qu'à un sondage.

        ⚠️ La garde est ici parce que rien n'empêche, à l'écran comme par RPC,
        de changer le type d'une publication après coup. Le formulaire cache
        l'onglet, ce qui n'a jamais gardé une donnée.
        """
        for option in self:
            if option.post_id.type_publication != "sondage":
                raise ValidationError(self.env._(
                    "Only a post of type \"Poll\" carries options."))
