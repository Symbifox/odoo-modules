# Part of bf_recruitment_expense. Voir LICENSE.
from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    recruitment_panel_hourly_cost = fields.Monetary(
        string="Taux horaire de repli du panel",
        currency_field="currency_id",
        default=0.0,
        help="Sert quand un membre de panel n'a pas de dossier d'employé dans "
             "cette société, ou que son coût horaire y est nul. Laissé à zéro, "
             "les heures de ces personnes ne sont pas valorisées, et le poste "
             "le dit au lieu de les compter pour rien.",
    )
