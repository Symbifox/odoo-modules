# -*- coding: utf-8 -*-
"""Refermer la boucle d'un canal manuel.

Sans ce geste, un billet publié à la main resterait « brouillon » pour
toujours : les mesures ne le rattraperaient jamais, et « dernière diffusion »
mentirait sur l'entrée éditoriale.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError

MANUAL_NETWORKS = ("linkedin_manual",)


class SocialPost(models.Model):
    _inherit = "bf.social.post"

    is_manual_channel = fields.Boolean(
        string="Canal manuel", compute="_compute_is_manual_channel",
    )
    manual_url = fields.Char(
        string="Post URL",
        help="The URL of the post as it was published on the network. It "
             "is the proof of publication: do not paste the article's URL "
             "here, it already lives in \"Shared link\".",
    )

    @api.depends("channel_id.network")
    def _compute_is_manual_channel(self):
        for post in self:
            post.is_manual_channel = post.channel_id.network in MANUAL_NETWORKS

    def action_mark_published_manually(self):
        """Consigner une publication faite à la main sur le réseau."""
        self.ensure_one()
        if not self.is_manual_channel:
            raise UserError(_(
                "This channel posts through the API: use \"Publish now\" "
                "instead of recording a manual post."))
        if self.remote_id:
            raise UserError(_("This post is already recorded as published."))
        url = (self.manual_url or "").strip()
        if not url:
            raise UserError(_(
                "First paste the post's URL in \"Post URL\": without it, "
                "nothing proves it went out."))
        if self.link_url and url == self.link_url:
            raise UserError(_(
                "This is the article's URL, not the post's. Paste the URL "
                "of the post as it was published on the network."))
        self.write({
            "state": "sent",
            "remote_id": url,
            "remote_url": url,
            "published_datetime": fields.Datetime.now(),
            "error_message": False,
        })
        self.message_post(body=_(
            "Manual post recorded on %(canal)s: %(url)s",
            canal=self.channel_id.name, url=url))
        return True
