from odoo import api, fields, models
from .gen_portees import LIBELLE_SANTE, PORTEE_SANTE


VITAL_UNITS = {
    "weight": "kg",
    "bp_systolic": "mmHg",
    "bp_diastolic": "mmHg",
    "heart_rate": "bpm",
    "blood_sugar": "mmol/L",
    "temperature": "°C",
    "spo2": "%",
    "water_ml": "mL",
    "calories": "kcal",
    "steps": "pas",
    "active_minutes": "min",
    "resting_hr": "bpm",
    "sleep_hours": "h",
}


class HealthVital(models.Model):
    _name = "health.vital"
    # Verrou de Gen (voir models/gen_portees.py).
    _gen_scope = PORTEE_SANTE
    _gen_scope_label = LIBELLE_SANTE
    _description = "Signe vital"
    _order = "date desc, id desc"

    date = fields.Datetime(
        string="Date", required=True, default=fields.Datetime.now
    )
    vital_type = fields.Selection(
        [
            ("weight", "Poids"),
            ("bp_systolic", "PA systolique"),
            ("bp_diastolic", "PA diastolique"),
            ("heart_rate", "Fréquence cardiaque"),
            ("blood_sugar", "Glycémie"),
            ("temperature", "Température"),
            ("spo2", "SpO2"),
            ("water_ml", "Eau"),
            ("calories", "Calories"),
            ("steps", "Pas"),
            ("active_minutes", "Minutes actives"),
            ("resting_hr", "FC au repos"),
            ("sleep_hours", "Sommeil"),
        ],
        string="Type",
        required=True,
    )
    value = fields.Float(string="Valeur", required=True, digits=(10, 2))
    unit = fields.Char(string="Unité", compute="_compute_unit", store=True)
    notes = fields.Char(string="Notes")
    year = fields.Integer(string="Année", compute="_compute_period", store=True)
    month = fields.Integer(string="Mois", compute="_compute_period", store=True)

    @api.depends("vital_type")
    def _compute_unit(self):
        for rec in self:
            rec.unit = VITAL_UNITS.get(rec.vital_type, "")

    @api.depends("date")
    def _compute_period(self):
        for rec in self:
            if rec.date:
                rec.year = rec.date.year
                rec.month = rec.date.month
            else:
                rec.year = 0
                rec.month = 0
