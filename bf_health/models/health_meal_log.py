from odoo import api, fields, models
from .gen_portees import LIBELLE_SANTE, PORTEE_SANTE


MEAL_TYPES = [
    ("breakfast", "Déjeuner"),
    ("lunch", "Dîner"),
    ("dinner", "Souper"),
    ("snack", "Collation"),
]


class HealthMealLog(models.Model):
    _name = "health.meal.log"
    # Verrou de Gen (voir models/gen_portees.py).
    _gen_scope = PORTEE_SANTE
    _gen_scope_label = LIBELLE_SANTE
    _inherit = ["bf.health.parent.guard"]
    _bf_champs_parents = ("food_id",)
    _description = "Entrée du journal alimentaire"
    _order = "date desc, id desc"

    date = fields.Date(
        string="Date", required=True, default=fields.Date.context_today
    )
    meal_type = fields.Selection(
        MEAL_TYPES, string="Repas", required=True, default="breakfast"
    )
    food_id = fields.Many2one(
        "health.food", string="Aliment", required=True, ondelete="restrict"
    )
    servings = fields.Float(string="Portions", default=1.0, digits=(10, 2))
    calories = fields.Float(
        string="Calories (kcal)", compute="_compute_nutrition", store=True, digits=(10, 2)
    )
    protein_g = fields.Float(
        string="Protéines (g)", compute="_compute_nutrition", store=True, digits=(10, 2)
    )
    carbs_g = fields.Float(
        string="Glucides (g)", compute="_compute_nutrition", store=True, digits=(10, 2)
    )
    fat_g = fields.Float(
        string="Lipides (g)", compute="_compute_nutrition", store=True, digits=(10, 2)
    )
    notes = fields.Char(string="Notes")
    year = fields.Integer(string="Année", compute="_compute_period", store=True)
    month = fields.Integer(string="Mois", compute="_compute_period", store=True)

    @api.depends(
        "servings",
        "food_id",
        "food_id.calories",
        "food_id.protein_g",
        "food_id.carbs_g",
        "food_id.fat_g",
    )
    def _compute_nutrition(self):
        for rec in self:
            food = rec.food_id
            mult = rec.servings or 0.0
            rec.calories = (food.calories or 0.0) * mult
            rec.protein_g = (food.protein_g or 0.0) * mult
            rec.carbs_g = (food.carbs_g or 0.0) * mult
            rec.fat_g = (food.fat_g or 0.0) * mult

    @api.depends("date")
    def _compute_period(self):
        for rec in self:
            if rec.date:
                rec.year = rec.date.year
                rec.month = rec.date.month
            else:
                rec.year = 0
                rec.month = 0

    @api.constrains("food_id")
    def _check_aliment_de_la_meme_personne(self):
        """On ne rattache son journal qu'à l'aliment qu'on peut lire.

        Les règles d'enregistrement ne sont rejouées ni après une écriture ni
        sur les champs reliés : sans ce contrôle, une personne du ménage
        pointait son journal vers la fiche d'une autre (ids séquentiels) et en
        relisait le nom ou les valeurs, calculés en superutilisateur. En
        superutilisateur (crons), `check_access` laisse passer.
        """
        for rec in self:
            rec.food_id.check_access("read")
