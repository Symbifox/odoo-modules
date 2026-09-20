# -*- coding: utf-8 -*-
from odoo import _, models


class CelebrationBoard(models.Model):
    _inherit = "bf.celebration.board"

    def _livrer(self):
        """Après la livraison, poser la carte au babillard."""
        resultat = super()._livrer()
        Post = self.env["bf.babillard.post"]
        for board in self:
            nom = board.sudo().recipient_name
            if not nom or board.sudo().state != "delivered":
                continue
            occasion = board.sudo().occasion_id.name if board.sudo().occasion_id else ""
            titre = (_("%(nom)s a reçu sa carte : %(occasion)s", nom=nom, occasion=occasion)
                     if occasion else _("%(nom)s a reçu sa carte", nom=nom))
            Post._depuis_source("bf.celebration.board", board.id, {
                "name": titre,
                "type_publication": "celebration",
                "audience": "tous",
                "company_id": board.sudo().company_id.id or self.env.company.id,
                "corps_html": "<p>%s</p>" % _(
                    "Le bureau a signé, la carte est partie."),
                # Le visage de la personne fêtée. Une célébration sans visage se
                # lit comme une ligne de journal.
                "personne_id": board.sudo().recipient_employee_id.id or False,
            })
        return resultat
