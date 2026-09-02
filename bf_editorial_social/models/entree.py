# -*- coding: utf-8 -*-
"""Ce que la diffusion ajoute à l'entrée éditoriale."""

from odoo import _, api, fields, models


class EditorialEntry(models.Model):
    _inherit = "bf.editorial.entry"

    blurb_ids = fields.One2many("bf.editorial.blurb", "entry_id", string="Blurbs")
    social_post_ids = fields.One2many(
        "bf.social.post", "entry_id", string="Billets sociaux",
    )
    social_post_count = fields.Integer(
        string="Diffusions", compute="_compute_social_post_count",
    )
    # Calcul SÉPARÉ du compteur : Odoo avertit à chaque chargement du registre
    # quand une même méthode calcule des champs de « store » différents.
    last_social_date = fields.Datetime(
        string="Dernière diffusion", compute="_compute_last_social_date", store=True,
    )

    @api.depends("social_post_ids.state")
    def _compute_social_post_count(self):
        for e in self:
            e.social_post_count = len(
                e.social_post_ids.filtered(lambda p: p.state == "sent"))

    @api.depends("social_post_ids.state", "social_post_ids.published_datetime")
    def _compute_last_social_date(self):
        for e in self:
            dates = e.social_post_ids.filtered(
                lambda p: p.state == "sent").mapped("published_datetime")
            e.last_social_date = max(dates) if dates else False

    def action_view_social_posts(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Diffusions"),
            "res_model": "bf.social.post",
            "view_mode": "list,form",
            "domain": [("entry_id", "=", self.id)],
            "context": {"default_entry_id": self.id},
        }
