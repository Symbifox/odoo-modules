# -*- coding: utf-8 -*-
from markupsafe import Markup

from odoo import _, models
from odoo.exceptions import AccessError, UserError


class Survey(models.Model):
    _inherit = "survey.survey"

    def action_annoncer_au_babillard(self):
        """Poser au fil la carte d'un sondage Odoo, une seule fois.

        🔴 Publique, donc appelable par RPC : le `groups=` du bouton ne garde
        que l'écran. Sans ce contrôle, n'importe quelle personne interne
        publiait au babillard de toute la maison en passant par ici.
        """
        self.ensure_one()
        if not self.env.user.has_group("bf_babillard.group_babillard_redacteur"):
            raise AccessError(_("Seule la rédaction publie au babillard."))
        if not self.active:
            raise UserError(_("Un sondage archivé ne s'annonce pas."))
        # 🔴 Le lien de départ d'un sondage « sur invitation » ne mène nulle
        # part sans le jeton personnel du répondant : la carte, elle, est lue
        # par toute l'audience. Mieux vaut refuser que publier un lien mort.
        if self.access_mode == "token":
            raise UserError(_(
                "Ce sondage est réservé aux personnes invitées : son lien ne "
                "fonctionne qu'avec l'invitation. Passez-le en « Toute personne "
                "disposant du lien » pour l'annoncer au fil."))

        base = self.env["ir.config_parameter"].sudo().get_param("web.base.url", "")
        lien = "%s%s" % (base, self.get_start_url())
        langue = self.env["bf.babillard.post"]._langue_de_la_maison()
        moi = self.with_context(lang=langue)

        corps = Markup("<p>%s</p><p><a href=\"%s\" class=\"btn btn-primary\">%s</a></p>") % (
            moi.env._("Un sondage vous attend : %(titre)s.", titre=self.title),
            lien,
            moi.env._("Répondre au sondage"),
        )
        carte = self.env["bf.babillard.post"]._depuis_source(
            "survey.survey", self.id, {
                "name": self.title,
                "type_publication": "annonce",
                "audience": "tous",
                "corps_html": corps,
                "company_id": self.env.company.id,
            })
        return {
            "type": "ir.actions.act_window",
            "res_model": "bf.babillard.post",
            "res_id": carte.id,
            "view_mode": "form",
            "target": "current",
        }
