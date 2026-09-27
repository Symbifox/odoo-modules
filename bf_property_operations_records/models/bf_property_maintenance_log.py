"""Ce que le carnet montre de la dérive, en un seul endroit.

⚠️ Une colonne dans une liste de deux cents biens ne se voit pas. Un carnet qui
signale la dérive uniquement là où il faut déjà la chercher est un carnet qui ne
la signale pas — c'est le défaut de classe déjà corrigé, où sept champs
écrits d'après le règlement n'avaient aucun écran.
"""
from odoo import api, fields, models


class BfPropertyMaintenanceLog(models.Model):
    _inherit = "bf.property.maintenance.log"

    equipment_drift_count = fields.Integer(
        string="Biens en écart avec l'exploitation",
        compute="_compute_equipment_drift_count",
        help="Nombre de biens du carnet dont l'exploitation a retiré "
             "l'équipement du service sans que le carnet ait noté de travaux "
             "réalisés.",
    )

    @api.depends("item_ids.equipment_drift")
    def _compute_equipment_drift_count(self):
        for log in self:
            log.equipment_drift_count = len(
                log.item_ids.filtered("equipment_drift")
            )

    bf_skipped_item_count = fields.Integer(
        string="Biens dont un entretien cédulé n'a pas été fait",
        compute="_compute_bf_skipped_item_count",
        help="Nombre de biens du carnet dont l'exploitation a déclaré un "
             "entretien préventif non effectué. L'art. 4 du règlement veut, à "
             "la mise à jour annuelle, ce qui n'a pas été fait ET pourquoi.",
    )

    @api.depends("item_ids.bf_skipped_count")
    def _compute_bf_skipped_item_count(self):
        for log in self:
            log.bf_skipped_item_count = len(
                log.item_ids.filtered(lambda item: item.bf_skipped_count)
            )
