# -*- coding: utf-8 -*-
"""La liste de contrôle : les « restes humains » qui traînent en fin de course.

Deux images à produire, une relecture, un lien croisé à poser une fois l'autre
article publié. Écrits en prose dans une note, ces restes se perdent. Ici ce
sont des lignes avec un état, et les bloquantes tiennent la garde de pré-vol.
"""

from odoo import _, api, fields, models


class EditorialChecklistTemplate(models.Model):
    _name = "bf.editorial.checklist.template"
    _description = "Gabarit de liste de contrôle"
    _order = "sequence, id"

    name = fields.Char(string="Intitulé", required=True, translate=True)
    sequence = fields.Integer(string="Séquence", default=10)
    pillar_ids = fields.Many2many(
        "blog.tag.category", string="Piliers",
        domain=[("is_pillar", "=", True)],
        help="Vide, la ligne s'applique à tous les piliers.",
    )
    kind = fields.Selection(
        [
            ("blog", "Article de blogue"),
            ("social", "Publication sociale"),
            ("newsletter", "Infolettre"),
            ("video", "Vidéo"),
        ],
        string="Nature",
        help="Vide, la ligne s'applique à toutes les natures.",
    )
    is_blocking = fields.Boolean(
        string="Bloquante", default=True,
        help="Bloquante, elle empêche la publication tant qu'elle est ouverte.",
    )
    active = fields.Boolean(string="Actif", default=True)


class EditorialChecklist(models.Model):
    _name = "bf.editorial.checklist"
    _description = "Ligne de liste de contrôle"
    _order = "sequence, id"

    entry_id = fields.Many2one(
        "bf.editorial.entry", string="Entrée", required=True,
        ondelete="cascade", index=True,
    )
    name = fields.Char(string="Intitulé", required=True)
    sequence = fields.Integer(string="Séquence", default=10)
    is_blocking = fields.Boolean(string="Bloquante", default=True)
    done = fields.Boolean(string="Fait", copy=False)
    done_by = fields.Many2one(
        "res.users", string="Fait par", readonly=True, copy=False,
    )
    done_date = fields.Datetime(string="Fait le", readonly=True, copy=False)
    note = fields.Char(string="Note")

    def write(self, vals):
        """Tracer qui a coché, et quand. Un reste coché sans trace n'en est
        plus un : c'est le point qui a manqué au lien croisé du billet 282."""
        if "done" in vals:
            if vals["done"]:
                vals.setdefault("done_by", self.env.uid)
                vals.setdefault("done_date", fields.Datetime.now())
            else:
                vals["done_by"] = False
                vals["done_date"] = False
        return super().write(vals)

    @api.model
    def _apply_templates(self, entry):
        """Poser les lignes de gabarit qui correspondent à l'entrée."""
        templates = self.env["bf.editorial.checklist.template"].search([
            "|", ("pillar_ids", "=", False), ("pillar_ids", "in", entry.pillar_id.ids),
            "|", ("kind", "=", False), ("kind", "=", entry.kind),
        ])
        existing = set(entry.checklist_ids.mapped("name"))
        created = self.browse()
        for template in templates:
            if template.name in existing:
                continue
            created |= self.create({
                "entry_id": entry.id,
                "name": template.name,
                "sequence": template.sequence,
                "is_blocking": template.is_blocking,
            })
        return created
