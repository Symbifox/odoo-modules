# -*- coding: utf-8 -*-
"""Suggérer le prochain article, à la racine du module.

Le classement de `bf_editorial` dit ce qui est le plus avancé. Il ne sait pas
si un angle a vieilli, ni qu'un pilier réclamé n'a aucun candidat. C'est ce
jugement-là que GenFox ajoute, et il le rend en toutes lettres.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class EditorialCalendar(models.Model):
    _inherit = "bf.editorial.calendar"

    genfox_available = fields.Boolean(
        string="Gen joignable", compute="_compute_genfox_available",
    )

    def _compute_genfox_available(self):
        joignable = self.env["bf.ai.bridge"].available()
        for calendar in self:
            calendar.genfox_available = joignable

    def action_genfox_propose(self):
        self.ensure_one()
        self.env["bf.editorial.suggestion"].launch("propose", calendar=self)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "info",
                "title": _("Gen est parti travailler"),
                "message": _(
                    "Gen lit le calendrier, le classement et le carnet"
                    " d'idées, puis rend sa recommandation. Elle apparaîtra"
                    " sous Atelier éditorial > Propositions Gen."
                ),
                "sticky": False,
                "next": {"type": "ir.actions.client", "tag": "soft_reload"},
            },
        }

    @api.model
    def action_genfox_propose_default(self):
        """Point d'entrée du menu racine : suggérer sans choisir un calendrier."""
        calendar = self.env["bf.editorial.proposal"]._default_calendar()
        if not calendar:
            raise UserError(_(
                "Aucun calendrier éditorial n'est défini : il n'y a rien à"
                " suggérer."
            ))
        return calendar.action_genfox_propose()
