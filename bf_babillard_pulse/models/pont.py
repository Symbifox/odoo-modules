# -*- coding: utf-8 -*-
from markupsafe import Markup

from odoo import _, models


class PulseCampaign(models.Model):
    _inherit = "bf.ex.pulse.campaign"

    def action_close(self):
        resultat = super().action_close()
        Post = self.env["bf.babillard.post"]
        langue = Post._langue_de_la_maison()
        for vague in self:
            scores = self.env["bf.ex.pulse.score"].sudo().search([
                ("campaign_id", "=", vague.id),
                ("is_displayable", "=", True),
                # ⚠️ Rien par segment : un score d'équipe se désanonymise par
                # soustraction dès qu'on sait qui était là.
                ("segment_key", "in", [False, ""]),
            ])
            if not scores:
                continue
            lignes = Markup("").join(
                Markup("<li>%s : %s</li>") % (
                    s.metric_id.with_context(lang=langue).name, s.display_score)
                for s in scores)
            Post._depuis_source("bf.ex.pulse.campaign", vague.id, {
                "name": _("Résultats du pulse : %(vague)s", vague=vague.display_name),
                "type_publication": "nouvelle",
                "audience": "tous",
                # Les résultats d'une vague restent dans la société sondée.
                "company_id": vague.company_id.id or self.env.company.id,
                "corps_html": Markup("<p>%s</p><ul>%s</ul>") % (
                    _("Les axes dont assez de personnes ont répondu."), lignes),
            })
        return resultat
