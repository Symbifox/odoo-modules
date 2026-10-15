# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
from odoo import fields, models

# Les quatre niveaux retenus (arbitrage du 2026-10-14), du
# plus fermé au plus ouvert. L'ordre compte : il se compare.
NIVEAUX = [
    ("freebusy", "Availability only"),
    ("titles", "Titles and locations"),
    ("details", "All details"),
    ("edit", "Edit"),
]
RANG = {cle: i for i, (cle, _libelle) in enumerate(NIVEAUX)}


class ResUsers(models.Model):
    _inherit = "res.users"

    bf_calendar_share_default = fields.Selection(
        [n for n in NIVEAUX if n[0] != "edit"],
        string="Colleagues see my calendar",
        default="details",
        required=True,
        help="What colleagues see of the meetings you organize, unless you "
             "share your calendar with them at another level. People invited "
             "to a meeting always see it in full, and a private meeting "
             "always shows as Busy. All details is Odoo's usual behaviour.")

    @property
    def SELF_READABLE_FIELDS(self):
        # Sans ces listes, « Mon profil » refuse de s'ouvrir aux non-administrateurs
        # dès qu'un champ de la vue n'y est pas (leçon tirée d'un module voisin).
        return super().SELF_READABLE_FIELDS + ["bf_calendar_share_default"]

    @property
    def SELF_WRITEABLE_FIELDS(self):
        return super().SELF_WRITEABLE_FIELDS + ["bf_calendar_share_default"]
