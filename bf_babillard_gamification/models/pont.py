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
            valeurs = {
                "name": titre,
                "type_publication": "reconnaissance",
                "audience": "tous",
                "corps_html": corps if badge.sudo().comment else False,
                # Un badge n'a pas de société : la carte suit celle de la personne
                # reconnue, pas celle où la personne qui remet travaillait ce jour-là.
                "company_id": badge.sudo().user_id.company_id.id or self.env.company.id,
                # 🔴 La carte était signée par le compte qui écrit, souvent un
                # compte technique : « Administrateur » félicitait Anouk à la
                # place de Félix. Une reconnaissance sans son auteur n'en est
                # plus une, et la recherche est nette là-dessus (28 % des
                # reconnaissances mémorables viennent du gestionnaire).
                "auteur_user_id": badge.sudo().sender_id.id,
                # Le visage de la personne reconnue, pas celui de qui remet :
                # c'est d'elle que la carte parle.
                #
                # 🔴 PAS `user_id.employee_id` : ce champ cherche l'employé dans
                # `env.company`, celle de qui REMET. Dans le cas même que la
                # ligne au-dessus prend soin de gérer (un badge remis depuis une
                # autre société, ou par un cron), il rend vide, et la carte perd
                # en silence le visage qu'on venait de lui donner.
                "personne_id": self._employe_de(badge.sudo().user_id).id or False,
                # L'image du badge tient lieu d'illustration, quand il en a une.
                # 🔴 `bin_size` : dans un contexte d'écran, Odoo rend la TAILLE
                # d'un binaire (« 12.34 Kb ») au lieu de son contenu. Le badge
                # remis depuis un formulaire posait donc cette chaîne en image.
                "image_couverture": badge.sudo().badge_id.with_context(
                    bin_size=False).image_1920 or False,
            }
            Post._depuis_source("gamification.badge.user", badge.id, valeurs)
        return badges

    @api.model
    def _employe_de(self, user):
        """L'employé d'une personne, dans SA société à elle."""
        return self.env["hr.employee"].sudo().search([
            ("user_id", "=", user.id),
            ("company_id", "=", user.company_id.id),
        ], limit=1)
