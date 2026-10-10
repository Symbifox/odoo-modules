from odoo import api, fields, models
from .gen_portees import LIBELLE_SANTE, PORTEE_SANTE


class HealthSubstanceLog(models.Model):
    _name = "health.substance.log"
    # Verrou de Gen (voir models/gen_portees.py).
    _gen_scope = PORTEE_SANTE
    _gen_scope_label = LIBELLE_SANTE
    _description = "Journal de consommation"
    # Le champ « Pour » (personne à charge).
    _inherit = ["bf.health.dependent.mixin"]
    _order = "date desc, id desc"

    date = fields.Date(
        string="Date", required=True, default=fields.Date.context_today
    )
    substance_type = fields.Selection(
        [
            ("alcohol", "Alcool"),
            ("cannabis", "Cannabis"),
            ("tobacco", "Tabac"),
            ("caffeine", "Caféine"),
        ],
        string="Substance",
        required=True,
    )
    quantity = fields.Float(string="Quantité", digits=(10, 1))
    quantity_unit = fields.Selection(
        [
            ("drinks", "Consommations"),
            ("mg", "mg"),
            ("cigarettes", "Cigarettes"),
            ("cups", "Tasses"),
            ("joints", "Joints"),
            ("gummies", "Jujubes"),
            ("other", "Autre"),
        ],
        string="Unité",
    )
    context = fields.Char(string="Contexte")
    notes = fields.Char(string="Notes")
    year = fields.Integer(string="Année", compute="_compute_period", store=True)
    month = fields.Integer(string="Mois", compute="_compute_period", store=True)

    @api.depends("date")
    def _compute_period(self):
        for rec in self:
            if rec.date:
                rec.year = rec.date.year
                rec.month = rec.date.month
            else:
                rec.year = 0
                rec.month = 0
