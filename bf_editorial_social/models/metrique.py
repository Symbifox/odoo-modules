# -*- coding: utf-8 -*-
"""Les mesures d'un billet, en série datée plutôt qu'en compteur.

Un compteur écrasé chaque jour ne dit pas comment un billet a vécu. Une
capture par jour le dit, et elle survit à la purge du réseau.
"""

from odoo import api, fields, models


class SocialMetric(models.Model):
    _name = "bf.social.metric"
    _description = "Social post metric"
    _order = "capture_date desc, id desc"

    post_id = fields.Many2one(
        "bf.social.post", string="Post", required=True,
        ondelete="cascade", index=True,
    )
    entry_id = fields.Many2one(
        "bf.editorial.entry", related="post_id.entry_id", store=True, index=True,
    )
    channel_id = fields.Many2one(
        "bf.social.channel", related="post_id.channel_id", store=True, index=True,
    )
    capture_date = fields.Date(
        string="Capture date", required=True,
        default=fields.Date.context_today,
    )
    impressions = fields.Integer(string="Impressions")
    likes = fields.Integer(string="Likes")
    reposts = fields.Integer(string="Reposts")
    replies = fields.Integer(string="Replies")
    clicks = fields.Integer(
        string="Clicks",
        help="Taken from the tracked link, not from the network: it is "
             "the only click count that does not depend on the platform's "
             "goodwill.",
    )

    _sql_constraints = [
        ("one_per_day",
         "UNIQUE(post_id, capture_date)",
         "Only one capture per post per day."),
    ]

    @api.model
    def _record(self, post, mesures):
        """Écrire la capture du jour, ou la mettre à jour si elle existe."""
        vals = {k: v for k, v in mesures.items()
                if k in ("impressions", "likes", "reposts", "replies")}
        if post.tracker_id:
            vals["clicks"] = post.tracker_id.count
        aujourdhui = fields.Date.context_today(self)
        existante = self.search([
            ("post_id", "=", post.id), ("capture_date", "=", aujourdhui),
        ], limit=1)
        if existante:
            existante.write(vals)
            return existante
        vals.update({"post_id": post.id, "capture_date": aujourdhui})
        return self.create(vals)
