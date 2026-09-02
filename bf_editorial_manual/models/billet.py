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
        string="Adresse de la publication",
        help="L'adresse du billet tel qu'il est sorti sur le réseau. C'est"
             " elle qui prouve la publication : ne pas y coller l'adresse de"
             " l'article, qui vit déjà dans « Lien diffusé ».",
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
                "Ce canal publie par API : utilisez « Diffuser maintenant »"
                " plutôt que de consigner une publication manuelle."))
        if self.remote_id:
            raise UserError(_("Ce billet est déjà consigné comme diffusé."))
        url = (self.manual_url or "").strip()
        if not url:
            raise UserError(_(
                "Collez d'abord l'adresse de la publication dans « Adresse de"
                " la publication » : sans elle, rien ne prouve qu'elle est"
                " sortie."))
        if self.link_url and url == self.link_url:
            raise UserError(_(
                "C'est l'adresse de l'article, pas celle de la publication."
                " Collez l'adresse du billet tel qu'il est sorti sur le"
                " réseau."))
        self.write({
            "state": "sent",
            "remote_id": url,
            "remote_url": url,
            "published_datetime": fields.Datetime.now(),
            "error_message": False,
        })
        self.message_post(body=_(
            "Publication manuelle consignée sur %(canal)s : %(url)s",
            canal=self.channel_id.name, url=url))
        return True
