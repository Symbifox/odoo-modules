# -*- coding: utf-8 -*-
"""Le lien de retour depuis un billet vers son entrée éditoriale."""

from odoo import _, api, fields, models


class BlogPost(models.Model):
    _inherit = "blog.post"

    editorial_entry_ids = fields.One2many(
        "bf.editorial.entry", "post_id", string="Entrées éditoriales",
    )
    editorial_entry_count = fields.Integer(
        string="Entrées", compute="_compute_editorial_entry_count",
    )

    @api.depends("editorial_entry_ids")
    def _compute_editorial_entry_count(self):
        for post in self:
            post.editorial_entry_count = len(post.editorial_entry_ids)

    def action_open_editorial_entry(self):
        self.ensure_one()
        entry = self.editorial_entry_ids[:1]
        if entry:
            return {
                "type": "ir.actions.act_window",
                "res_model": "bf.editorial.entry",
                "res_id": entry.id,
                "view_mode": "form",
            }
        return {
            "type": "ir.actions.act_window",
            "name": _("Nouvelle entrée éditoriale"),
            "res_model": "bf.editorial.entry",
            "view_mode": "form",
            "context": {
                "default_post_id": self.id,
                "default_name": self.name,
                "default_kind": "blog",
            },
        }

    def write(self, vals):
        """Une écriture humaine sur le contenu invalide la QA.

        L'éditeur de site peut propager une chaîne d'une langue dans le créneau
        d'une autre. Une QA passée avant cette écriture ne dit donc plus rien
        de l'état actuel : on la remet à « à passer » plutôt que de la laisser
        afficher un vert périmé.
        """
        result = super().write(vals)
        if "content" in vals or "name" in vals:
            entries = self.mapped("editorial_entry_ids").filtered(
                lambda e: e.qa_state != "todo"
            )
            if entries:
                entries.write({"qa_state": "todo"})
                for entry in entries:
                    entry.message_post(body=_(
                        "Le contenu du billet a changé : la QA éditoriale est"
                        " à repasser."
                    ))
        return result
