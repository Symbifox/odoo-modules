"""Ce que le règlement a besoin de savoir de l'immeuble.

Trois données, et trois seulement, servent à départager les intervalles de
révision du carnet d'entretien (RLRQ, c. CCQ, r. 8.01, art. 5 al. 2). Elles
vivent ici plutôt qu'au socle parce qu'elles n'ont d'usage que réglementaire.

⚠️ Aucune des trois ne se déduit de ce que le socle porte déjà.

- « au plus 3 étages **entièrement hors sol** » n'est pas le nombre d'étages du
  socle : un immeuble de quatre niveaux dont un est en sous-sol en compte trois
  hors sol. Le socle ne sait pas lequel.
- « aucune partie commune **située dans un bâtiment** » ne se lit pas dans la
  liste des parties communes : une cour, une allée ou un stationnement extérieur
  sont des parties communes qui ne sont dans aucun bâtiment.
- « au plus 8 parties privatives, **excluant celles qui sont accessoires** à ces
  dernières tels les espaces de rangement et de stationnement » : le décompte
  se fait donc sur les fractions principales. C'est le seul des trois que le
  module peut calculer, à partir du type de fraction.
"""
from odoo import api, fields, models

# r. 8.01, art. 5 al. 2, par. 1° : les accessoires ne comptent pas. Le règlement
# nomme le rangement et le stationnement à titre d'exemple.
ACCESSORY_UNIT_TYPES = ("parking", "storage")


class BfPropertyBuilding(models.Model):
    _inherit = "bf.property.building"

    floors_above_ground = fields.Integer(
        string="Étages entièrement hors sol",
        tracking=True,
        help="RLRQ, c. CCQ, r. 8.01, art. 5 al. 2, par. 3° : « au plus 3 "
             "étages entièrement hors sol » permet la révision du carnet aux "
             "10 ans. Ce n'est pas le nombre d'étages : un niveau en sous-sol "
             "n'est pas hors sol. Saisi à la main, le module ne le devine pas.",
    )
    common_areas_in_building = fields.Boolean(
        string="Parties communes dans un bâtiment",
        default=True,
        tracking=True,
        help="Art. 5 al. 2, par. 2° : « aucune partie commune de l'immeuble "
             "n'est située dans un bâtiment » permet la révision aux 10 ans. "
             "Une cour, une allée ou un stationnement extérieur sont des "
             "parties communes qui ne sont dans aucun bâtiment. Le module ne "
             "peut pas le lire dans la liste des parties communes.",
    )
    private_unit_count = fields.Integer(
        string="Parties privatives (hors accessoires)",
        compute="_compute_private_unit_count",
        store=True,
        help="Art. 5 al. 2, par. 1° : le décompte exclut les fractions "
             "accessoires, rangements et stationnements compris. C'est ce "
             "décompte-là, et non le nombre total de fractions, qui décide de "
             "l'intervalle de révision.",
    )

    @api.depends("unit_ids.unit_type", "unit_ids.active")
    def _compute_private_unit_count(self):
        for building in self:
            building.private_unit_count = len(
                building.unit_ids.filtered(
                    lambda u: u.active and u.unit_type not in ACCESSORY_UNIT_TYPES
                )
            )
