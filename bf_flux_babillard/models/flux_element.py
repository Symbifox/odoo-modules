# -*- coding: utf-8 -*-
from markupsafe import Markup

from odoo import _, models
from odoo.exceptions import AccessError


class FluxElement(models.Model):
    _inherit = "bf.flux.element"

    def action_publier_babillard(self):
        """Prépare le brouillon du babillard pour cet élément, ou le rouvre."""
        self.ensure_one()
        # `_depuis_source` travaille en superutilisateur : le droit se vérifie ici.
        if not self.env.user.has_group("bf_babillard.group_babillard_redacteur"):
            raise AccessError(_("Seule la rédaction du babillard peut y préparer une carte."))
        services = self.retenue_ids.filtered(lambda r: r.etat == "retenu").liste_id.department_ids
        corps = Markup("")
        if self.resume:
            corps += Markup("<p>%s</p>") % self.resume
        corps += Markup("<p><a href='%s' target='_blank'>%s</a>%s</p>") % (
            self.lien, _("Lire la suite"),
            Markup(" · %s") % self.emetteur if self.emetteur else "")
        carte = self.env["bf.babillard.post"]._depuis_source("bf.flux.element", self.id, {
            "name": self.titre[:250],
            "corps_html": corps,
            "type_publication": "nouvelle",
            "audience": "departements" if services else "tous",
            "department_ids": [(6, 0, services.ids)],
            # Un brouillon : la rédaction relit, choisit l'échéance et publie.
            "state": "brouillon",
            "date_publication": False,
        })
        return {
            "type": "ir.actions.act_window",
            "res_model": "bf.babillard.post",
            "res_id": carte.id,
            "view_mode": "form",
            "target": "current",
        }


class FluxRetenue(models.Model):
    _inherit = "bf.flux.retenue"

    def action_publier_babillard(self):
        self.ensure_one()
        return self.element_id.action_publier_babillard()
