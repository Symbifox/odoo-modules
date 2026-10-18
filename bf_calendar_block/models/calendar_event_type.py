# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
"""Un type de plage de travail est une étiquette d'agenda.

Pas un modèle à part : l'étiquette est ce qui voyage déjà (CATEGORIES vers
Nextcloud et les autres clients), ce que la création rapide offre déjà, et ce
que bf_color sait déjà colorer. Le type ajoute à l'étiquette ce qu'une plage
fait par défaut.
"""

from odoo import api, fields, models


class CalendarEventType(models.Model):
    _inherit = "calendar.event.type"
    # Les étiquettes ordinaires d'abord, dans l'ordre du cœur (par id), puis
    # les types de plage dans leur ordre : celui des badges de la création
    # rapide. Les étiquettes ordinaires ne reculent pas derrière les types
    # dans l'autocomplétion.
    _order = "bf_is_block, bf_block_sequence, id"

    bf_is_block = fields.Boolean(
        string="Work block",
        help="Events with this tag are blocks of time booked for a kind of "
             "work (focus, production, catch-up…): they show its pictogram "
             "and colour in the calendar.")
    bf_block_glyph = fields.Char(
        string="Pictogram", size=4,
        help="Shown before the title of the block in the calendar.")
    bf_block_sequence = fields.Integer(
        string="Block order", default=10,
        help="Order of the block types in the quick-create dialog. When an "
             "event carries several block tags, the first one is its type.")
    bf_block_show_as = fields.Selection(
        [("busy", "Busy"), ("free", "Available")],
        string="Shown as", default="busy",
        help="Availability given to a new block of this type. A busy block "
             "is not offered for online booking.")
    bf_block_privacy = fields.Selection(
        [("public", "Public"), ("private", "Private"),
         ("confidential", "Only internal users")],
        string="Privacy",
        help="Privacy given to a new block of this type. Empty: the "
             "organizer's default privacy.")

    @api.depends("bf_is_block", "bf_block_glyph")
    def _compute_display_name(self):
        super()._compute_display_name()
        # Le pictogramme devant le nom partout où l'étiquette s'affiche
        # (badges de la création rapide, étiquettes, filtres) : c'est ce qui
        # dit qu'elle est un type de plage. Le nom, lui, reste le nom : c'est
        # lui qui part vers Nextcloud en CATEGORIES.
        for record in self.filtered(lambda t: t.bf_is_block and t.bf_block_glyph):
            record.display_name = "%s %s" % (record.bf_block_glyph, record.display_name)
