"""La citation, vue depuis l'exploitation.

Savoir qu'un équipement est cité par un carnet d'entretien change ce qu'on en
fait : une mise au rebut n'est plus seulement une ligne d'inventaire, c'est un
carnet réglementaire qui va cesser de dire ce qui est.
"""
from odoo import fields, models


class MaintenanceEquipment(models.Model):
    _inherit = "maintenance.equipment"

    bf_maintenance_item_ids = fields.One2many(
        "bf.property.maintenance.item",
        "equipment_id",
        string="Biens au carnet",
        help="Les entrées de carnet d'entretien qui citent cet équipement. Un "
             "carnet neuf remplace le précédent sans l'effacer : le même "
             "équipement peut donc être cité par plusieurs carnets.",
    )
