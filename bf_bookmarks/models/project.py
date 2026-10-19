# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError


class Project(models.Model):
    _inherit = "project.project"

    bookmark_count = fields.Integer(string="Signets", compute="_compute_bookmark_count")

    # 🔴 Le compte dépend de qui regarde (les règles de lecture) : sans
    # depends_context, le cache le resservait tel quel à un autre usager dans la
    # même transaction (trouvé par l'essai, 1 au lieu de 0 pour l'externe).
    @api.depends_context("uid")
    def _compute_bookmark_count(self):
        # Lu par tout ce qui lit un projet, portail compris : sans droit sur les
        # signets, zéro plutôt qu'une erreur d'accès sur la fiche entière.
        if not self.env["bf.bookmark"].has_access("read"):
            self.bookmark_count = 0
            return
        counts = dict(self.env["bf.bookmark"]._read_group(
            [("project_id", "in", self.ids)], ["project_id"], ["__count"]))
        for project in self:
            project.bookmark_count = counts.get(project, 0)

    def action_open_bookmarks(self):
        """Les signets de l'équipe du projet. La racine est créée au premier clic
        par qui en a le droit (le chargé de projet, un responsable des signets) ;
        pour les autres, la liste s'ouvre en lecture."""
        self.ensure_one()
        Folder = self.env["bf.bookmark.folder"]
        root = Folder.search([("parent_id", "=", False), ("scope", "=", "project"),
                              ("project_id", "=", self.id)], limit=1)
        if not root:
            try:
                with self.env.cr.savepoint():
                    root = Folder.create({"scope": "project", "project_id": self.id})
            except (AccessError, UserError, ValidationError):
                root = Folder.browse()
        # À plat : un projet n'a qu'une racine, et une liste groupée s'ouvre repliée,
        # ce qui fait croire à un projet sans signets (vu au parcours navigateur).
        context = {}
        if root and root.can_edit:
            context["default_folder_id"] = root.id
        return {
            "type": "ir.actions.act_window",
            "name": _("Signets de %s", self.name),
            "res_model": "bf.bookmark",
            "view_mode": "list,form",
            "domain": [("project_id", "=", self.id)],
            "context": context,
        }
