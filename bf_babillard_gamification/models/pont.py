# -*- coding: utf-8 -*-
from markupsafe import Markup

from odoo import _, api, models


class GamificationBadgeUser(models.Model):
    _inherit = "gamification.badge.user"

    @api.model_create_multi
    def create(self, vals_list):
        badges = super().create(vals_list)
        Post = self.env["bf.babillard.post"]
        langue = Post._langue_de_la_maison()
        for badge in badges:
            # Un badge attribué par une machine (défi, objectif) n'est pas une
            # reconnaissance entre personnes : on ne le publie pas.
            if not badge.sudo().sender_id:
                continue
            destinataire = badge.sudo().user_id.name
            remettant = badge.sudo().sender_id.name
            nom_du_badge = badge.sudo().badge_id.with_context(lang=langue).name
            titre = _("%(qui)s a remis le badge « %(badge)s » à %(destinataire)s",
                      qui=remettant, badge=nom_du_badge,
                      destinataire=destinataire)
            corps = Markup("<p>%s</p>") % (badge.sudo().comment or "")
            Post._depuis_source("gamification.badge.user", badge.id, {
                "name": titre,
                "type_publication": "reconnaissance",
                "audience": "tous",
                "corps_html": corps if badge.sudo().comment else False,
                # Un badge n'a pas de société : la carte suit celle de la personne
                # reconnue, pas celle où la personne qui remet travaillait ce jour-là.
                "company_id": badge.sudo().user_id.company_id.id or self.env.company.id,
            })
        return badges
