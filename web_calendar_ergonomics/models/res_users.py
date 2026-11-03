# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
"""Préférences de surlignage, par usager.

Le surlignage lui-même ne se stocke nulle part (il meurt avec la page) ; ces
deux champs disent seulement À QUOI il ressemble et QUAND il s'efface. Côté
serveur plutôt qu'en `localStorage` : la préférence suit l'usager d'un appareil
à l'autre, l'écran des préférences et le menu du calendrier écrivent la même
valeur, et un administrateur peut en poser une par défaut pour un collègue.
"""

from odoo import fields, models

# Champs que chaque usager lit et écrit sur SON profil : des préférences
# d'affichage, rien de plus (le menu du calendrier écrit par là).
CHAMPS_PROPRES = ["bf_calendar_highlight", "bf_calendar_highlight_clear"]


class ResUsers(models.Model):
    _inherit = "res.users"

    bf_calendar_highlight = fields.Selection(
        [
            ("ring", "Outline and dot"),
            ("pulse", "Brief pulse, then outline"),
            ("none", "No highlight"),
        ],
        string="Highlight calendar changes",
        default="ring",
        required=True,
        help="How the calendar marks what changed since the last refresh. The "
             "marks are never stored: they go away when the page is left.",
    )
    bf_calendar_highlight_clear = fields.Selection(
        # ⚠️ Mêmes mots que le menu du ⟳ (static/src/calendar_controller.xml) :
        # deux vocabulaires pour un seul réglage se lisent comme deux réglages.
        [
            ("refresh", "At the next refresh"),
            ("stay", "When the page is left"),
            ("click", "On click, event by event"),
        ],
        string="Clear calendar marks",
        default="refresh",
        required=True,
        help="When the marks go away. Each refresh replaces them by what "
             "changed since that refresh, they can instead pile up for as long "
             "as the page is open, or a click on an event can clear its own "
             "mark.",
    )

    @property
    def SELF_READABLE_FIELDS(self):
        return super().SELF_READABLE_FIELDS + CHAMPS_PROPRES

    @property
    def SELF_WRITEABLE_FIELDS(self):
        return super().SELF_WRITEABLE_FIELDS + CHAMPS_PROPRES
