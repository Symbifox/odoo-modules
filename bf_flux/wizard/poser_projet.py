# -*- coding: utf-8 -*-
from markupsafe import Markup

from odoo import _, fields, models


class FluxPoserProjet(models.TransientModel):
    _name = "bf.flux.poser.projet"
    _description = "Poser un élément de flux au fil d'un projet"

    element_id = fields.Many2one("bf.flux.element", required=True, readonly=True)
    project_id = fields.Many2one("project.project", string="Projet", required=True)
    commentaire = fields.Text("Mot d'accompagnement")

    def action_poser(self):
        self.ensure_one()
        elem = self.element_id
        corps = Markup("")
        if self.commentaire:
            corps += Markup("<p>%s</p>") % self.commentaire
        corps += Markup("<p><b><a href='%s' target='_blank'>%s</a></b></p>") % (
            elem.lien, elem.titre)
        if elem.emetteur:
            corps += Markup("<p><i>%s</i></p>") % elem.emetteur
        if elem.resume:
            corps += Markup("<p>%s</p>") % elem.resume[:600]
        self.project_id.message_post(
            body=corps, message_type="comment", subtype_xmlid="mail.mt_note")
        return {
            "type": "ir.actions.client", "tag": "display_notification",
            "params": {
                "message": _("Posé au fil de « %s ».", self.project_id.display_name),
                "type": "success",
                "next": {"type": "ir.actions.act_window_close"},
            },
        }
