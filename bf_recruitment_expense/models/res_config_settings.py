# Part of bf_recruitment_expense. Voir LICENSE.
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    # ⚠️ `res.config.settings` ne porte PAS `company_currency_id` de base : c'est
    # `purchase` qui l'ajoute, et rien ne garantit sa présence ici. On le
    # déclare donc soi-même. Deux définitions identiques du même champ related
    # fusionnent sans conflit, alors ça reste sans effet là où il existe déjà.
    company_currency_id = fields.Many2one(
        "res.currency", related="company_id.currency_id",
        string="Devise de la société", readonly=True,
    )
    recruitment_panel_hourly_cost = fields.Monetary(
        related="company_id.recruitment_panel_hourly_cost",
        string="Taux horaire de repli du panel",
        currency_field="company_currency_id",
        readonly=False,
    )
