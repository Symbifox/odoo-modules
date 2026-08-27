# -*- coding: utf-8 -*-
"""Piliers et sujets, posés sur la taxonomie native du blogue.

Odoo fournit déjà deux niveaux — ``blog.tag.category`` et ``blog.tag`` — que
le site public sait afficher et filtrer. Les réutiliser plutôt que d'inventer
un modèle évite une taxonomie fantôme visible du seul back-office.

Le pilier est la catégorie ; le sujet est l'étiquette.
"""

from odoo import api, fields, models


class BlogTagCategory(models.Model):
    """Un pilier éditorial : découverte, produit, posture."""

    _inherit = "blog.tag.category"

    is_pillar = fields.Boolean(
        string="Pilier éditorial",
        help="Coché, cette catégorie compte dans le ratio d'un calendrier.",
    )
    target_share = fields.Float(
        string="Cible (%)",
        help="Part visée de la production, en pourcentage. La somme des cibles"
             " d'un calendrier devrait faire 100, sans quoi le ratio se lit mal.",
    )
    color = fields.Integer(string="Couleur")
    entry_count = fields.Integer(
        string="Entrées", compute="_compute_entry_count",
    )

    @api.depends("tag_ids.post_ids")
    def _compute_entry_count(self):
        grouped = self.env["bf.editorial.entry"]._read_group(
            [("pillar_id", "in", self.ids)], ["pillar_id"], ["__count"],
        )
        counts = {pillar.id: count for pillar, count in grouped}
        for category in self:
            category.entry_count = counts.get(category.id, 0)


class BlogTag(models.Model):
    """Un sujet. C'est l'axe « mot-clic » de l'analytique."""

    _inherit = "blog.tag"

    entry_count = fields.Integer(
        string="Entrées", compute="_compute_entry_count",
    )

    def _compute_entry_count(self):
        for tag in self:
            tag.entry_count = self.env["bf.editorial.entry"].search_count(
                [("tag_ids", "in", tag.id)]
            )
