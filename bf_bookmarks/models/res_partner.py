# -*- coding: utf-8 -*-
from odoo import _, api, fields, models


class ResPartner(models.Model):
    _inherit = "res.partner"

    bookmark_count = fields.Integer(string="Signets", compute="_compute_bookmark_count")

    def _bookmark_domain(self):
        """Les signets des projets de ce client (et de ses contacts)."""
        self.ensure_one()
        return [("project_id.partner_id", "child_of", self.commercial_partner_id.id)]

    # 🔴 Le compte dépend de qui regarde (les règles de lecture) : sans
    # depends_context, le cache le resservait tel quel à un autre usager dans la
    # même transaction (trouvé par l'essai, 1 au lieu de 0 pour l'externe).
    @api.depends_context("uid")
    def _compute_bookmark_count(self):
        Bookmark = self.env["bf.bookmark"]
        # Voir project.project : un contact est lu par le portail et le site.
        if not Bookmark.has_access("read"):
            self.bookmark_count = 0
            return
        for partner in self:
            partner.bookmark_count = (
                Bookmark.search_count(partner._bookmark_domain()) if partner.id else 0)

    def action_open_bookmarks(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Signets de %s", self.commercial_partner_id.name),
            "res_model": "bf.bookmark",
            "view_mode": "list,form",
            "domain": self._bookmark_domain(),
            "context": {"search_default_group_folder": 1},
        }
