# -*- coding: utf-8 -*-
"""Ce que l'audience ajoute à une entrée éditoriale.

Le socle porte déjà ``raw_visits``, le compteur natif d'Odoo, avec sa mise en
garde : il compte les robots. On ne le remplace pas et on ne le corrige pas
rétroactivement — on ne sait pas ce qu'il contenait. On l'accompagne.
"""

from odoo import api, fields, models


class EditorialEntry(models.Model):
    _inherit = "bf.editorial.entry"

    audience_ids = fields.One2many(
        "bf.editorial.audience", "entry_id", string="Relevés d'audience",
    )
    audience_tracked = fields.Integer(
        string="Vues retenues", compute="_compute_audience", store=True,
        help="Ce qu'Odoo a tracé, une fois sa propre liste de robots"
             " appliquée. Le brut, lui, est le compteur natif du billet.",
    )
    audience_human = fields.Integer(
        string="Vues humaines", compute="_compute_audience", store=True,
        help="Les vues dont le visiteur s'est déclaré navigateur. Ni les"
             " robots, ni ceux dont l'agent n'a pas été relevé.",
    )
    audience_bot = fields.Integer(
        string="Robots passés au travers", compute="_compute_audience",
        store=True,
        help="Les robots que la liste d'Odoo ne nomme pas.",
    )
    audience_unknown = fields.Integer(
        string="Vues à agent non relevé", compute="_compute_audience",
        store=True,
        help="Le seau de l'honnêteté : tout ce qu'on n'a pas su lire, y"
             " compris toutes les visites d'avant la mise en service de la"
             " capture.",
    )
    audience_bot_share = fields.Float(
        string="Part des robots passés au travers", compute="_compute_audience", store=True,
        digits=(5, 2), aggregator="avg",
    )
    audience_first_day = fields.Date(
        string="Premier relevé", compute="_compute_audience", store=True,
    )

    @api.depends(
        "audience_ids.tracked_views", "audience_ids.human_views",
        "audience_ids.bot_views", "audience_ids.unknown_views",
        "audience_ids.capture_date",
    )
    def _compute_audience(self):
        for entree in self:
            releves = entree.audience_ids
            brut = sum(releves.mapped("tracked_views"))
            robots = sum(releves.mapped("bot_views"))
            entree.audience_tracked = brut
            entree.audience_human = sum(releves.mapped("human_views"))
            entree.audience_bot = robots
            entree.audience_unknown = sum(releves.mapped("unknown_views"))
            entree.audience_bot_share = 100.0 * robots / brut if brut else 0.0
            dates = releves.mapped("capture_date")
            entree.audience_first_day = min(dates) if dates else False
