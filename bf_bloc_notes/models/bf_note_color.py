"""Couleurs libres du bloc-notes, par ``bf_color``.

Les notes gardent leur index 0-11, mais il se lit dans la palette PASTEL de
Symbifox Mobile : c'est ce que le téléphone
montre déjà, et un fond pâle reste lisible en carte pleine. Aucune donnée n'est
réécrite : une note sans ``color_hex`` prend le pastel de son index.

Les étiquettes de notes restent sur la palette d'Odoo, comme toutes les
étiquettes.
"""

from odoo import api, models

#: `PALETTE_NOTES` de Symbifox Mobile (NotesModels.kt), 0 = aucune.
NOTE_PALETTE = [
    "#FFFFFF", "#F4A3A3", "#F7CD9F", "#FCE89A", "#B6D7F2", "#D5B4E3",
    "#F3C4B2", "#A9DCD1", "#9FB5D9", "#E6A8C8", "#B9E3B0", "#D1C4E9",
]


class BfNote(models.Model):
    _name = "bf.note"
    _inherit = ["bf.note", "bf.color.mixin"]

    @api.model
    def _bf_color_palette(self):
        return NOTE_PALETTE


class BfNoteTag(models.Model):
    _name = "bf.note.tag"
    _inherit = ["bf.note.tag", "bf.color.mixin"]
