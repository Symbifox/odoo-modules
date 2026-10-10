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
        "bf.editorial.audience", "entry_id", string="Audience snapshots",
    )
    audience_tracked = fields.Integer(
        string="Tracked views", compute="_compute_audience", store=True,
        help="What Odoo tracked, once its own bot list was applied. The "
             "raw figure is the post's native counter.",
    )
    audience_human = fields.Integer(
        string="Human views", compute="_compute_audience", store=True,
        help="Views whose visitor declared itself a browser. Neither bots "
             "nor those whose agent was not recorded.",
    )
    audience_bot = fields.Integer(
        string="Bots that got through", compute="_compute_audience",
        store=True,
        help="The bots Odoo's list does not name.",
    )
    audience_unknown = fields.Integer(
        string="Views with an unrecorded agent", compute="_compute_audience",
        store=True,
        help="The honesty bucket: everything that could not be read, "
             "including all visits from before the capture went live.",
    )
    audience_bot_share = fields.Float(
        string="Share of bots that got through", compute="_compute_audience", store=True,
        digits=(5, 2), aggregator="avg",
    )
    audience_first_day = fields.Date(
        string="First snapshot", compute="_compute_audience", store=True,
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
