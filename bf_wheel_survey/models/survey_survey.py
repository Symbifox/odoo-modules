# -*- coding: utf-8 -*-
from odoo import _, models
from odoo.exceptions import AccessError


class SurveySurvey(models.Model):
    _inherit = "survey.survey"

    def action_bf_wheel_session(self):
        """Ouvre la roue des participants de ce sondage, en la créant au besoin."""
        self.ensure_one()
        Roue = self.env["bf.wheel"]
        roue = Roue.search(
            [("survey_id", "=", self.id), ("source", "=", "session")], limit=1)
        if not roue:
            # ⚠️ On demande à l'ORM s'il laisserait créer, au lieu de relire le
            # groupe à la main : le groupe dit « non » à l'administrateur système,
            # qui a pourtant tous les droits, et une garde qui double une règle
            # d'accès finit toujours par diverger d'elle.
            if not Roue.has_access("create"):
                raise AccessError(_(
                    "Aucune roue n'est encore rattachée à « %(sondage)s », et en "
                    "créer une demande le groupe « Roue de fortune / Responsable ».",
                    sondage=self.title))
            roue = Roue.create({
                "name": _("Tirage : %(sondage)s", sondage=self.title),
                "source": "session",
                "survey_id": self.id,
            })
        return roue.action_open_projector()
