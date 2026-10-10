from odoo import fields, models
from .gen_portees import LIBELLE_SANTE, PORTEE_SANTE


class HealthNutritionGoal(models.Model):
    _name = "health.nutrition.goal"
    # Verrou de Gen (voir models/gen_portees.py).
    _gen_scope = PORTEE_SANTE
    _gen_scope_label = LIBELLE_SANTE
    _description = "Objectif nutritionnel"
    _order = "id desc"

    name = fields.Char(string="Nom", required=True, default="Mon objectif")
    daily_calorie_goal = fields.Integer(
        string="Objectif calorique (kcal/jour)", default=2000
    )
    protein_target_g = fields.Integer(string="Protéines cibles (g)")
    carbs_target_g = fields.Integer(string="Glucides cibles (g)")
    fat_target_g = fields.Integer(string="Lipides cibles (g)")
    active = fields.Boolean(string="Actif", default=True)
