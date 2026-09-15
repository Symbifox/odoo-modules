# -*- coding: utf-8 -*-
from odoo import api, fields, models


class BabillardLecture(models.Model):
    """L'accusé de lecture d'une personne, sur une publication.

    ⚠️ Cette table est une **preuve de diffusion**, et rien d'autre. Elle ne porte
    ni score, ni durée de lecture, ni compte de visites : la Loi 25 encadre le
    profilage, qui inclut l'évaluation du rendement au travail, et un babillard
    n'a aucune raison d'y toucher.
    """

    _name = "bf.babillard.lecture"
    _description = "Accusé de lecture du babillard"
    _order = "date desc, id desc"
    _rec_name = "post_id"

    post_id = fields.Many2one(
        "bf.babillard.post", string="Publication", required=True,
        ondelete="cascade", index=True)
    user_id = fields.Many2one(
        "res.users", string="Personne", required=True, ondelete="cascade", index=True)
    date = fields.Datetime("Confirmée le", required=True, default=fields.Datetime.now)
    # Porté pour la règle multi-société : un accusé suit la société de sa publication.
    company_id = fields.Many2one(
        related="post_id.company_id", store=True, string="Société")

    _sql_constraints = [
        ("lecture_unique", "UNIQUE(post_id, user_id)",
         "Une personne ne confirme qu'une fois la lecture d'une publication."),
    ]

    @api.model_create_multi
    def create(self, vals_list):
        # 🔴 Le personnel n'a plus le droit de créer un accusé : il le donne par
        # le bouton « J'ai lu », qui vérifie l'état, l'audience et la lecture
        # obligatoire. Écrire dans la table sautait ces trois gardes. Cette ligne
        # reste en seconde défense : on ne pose pas un accusé au nom d'un autre.
        for vals in vals_list:
            if not self.env.su:
                vals["user_id"] = self.env.uid
        return super().create(vals_list)
