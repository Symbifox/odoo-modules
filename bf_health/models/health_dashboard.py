from odoo import _, api, fields, models
from odoo.exceptions import AccessError

from .parent_guard import fermee_a_cet_appel


#: Ce que le tableau lit en SQL brut : chacun doit être ouvert en entier.
MODELES_LUS = (
    "health.condition", "health.meal.log", "health.medication",
    "health.medication.log", "health.nutrition.goal", "health.screening",
    "health.substance.log", "health.vital", "health.workout",
)


class HealthDashboard(models.AbstractModel):
    _name = "health.dashboard"
    _description = "Tableau de bord santé"
    # `AbstractModel` : ce modèle n'a ni champ ni table, il ne sert que de point
    # d'entrée RPC pour le composant OWL. Déclaré `models.Model` + `_auto = False`,
    # il entrait dans `Registry.check_tables_exist()`, qui ne dispense que
    # `_abstract` et les modèles à `_table_query` — d'où un `ERROR
    # odoo.modules.registry: Model <ce modèle> has no table.` journalisé à chaque
    # passe du chargeur sur une base neuve.

    @api.model
    def get_dashboard_data(self, dependent_id=False):
        """Le tableau de la personne courante, ou de son enfant de moins de
        14 ans : chaque requête filtre sur ``dependent_id``, NULL
        pour ses propres fiches."""
        # Ce tableau lit la santé en SQL brut, à côté des règles.
        # Si le verrou de Gen ferme la santé à cet appel (canal API sans
        # permission), on refuse ici : sinon Gen lirait par ce détour ce que la
        # règle lui ferme. Sans Gen installé, la règle n'ajoute rien.
        if fermee_a_cet_appel(self.env, MODELES_LUS):
            raise AccessError(_("Healthy Fox est fermé à cet appel."))
        today = fields.Date.context_today(self)
        dependant = self._bf_personne_affichee(dependent_id)
        dep_id = dependant.id or None
        # Les fiches d'un enfant ont toutes pour auteur le parent qui les tient,
        # même celles que le second parent a notées.
        uid = dependant.sudo().create_uid.id if dependant else self.env.uid

        # --- Med compliance today ---
        self.env.cr.execute("""
            SELECT
                COUNT(*) FILTER (WHERE taken = TRUE) AS taken,
                COUNT(*) AS total
            FROM health_medication_log
            WHERE date = %s AND create_uid = %s AND dependent_id IS NOT DISTINCT FROM %s
        """, (today, uid, dep_id))
        med_row = self.env.cr.dictfetchone()
        med_compliance = {
            "taken": med_row["taken"] or 0,
            "total": med_row["total"] or 0,
        }

        # --- Latest vitals snapshot ---
        vitals_snapshot = {}
        for vtype in ("weight", "bp_systolic", "bp_diastolic", "heart_rate",
                       "blood_sugar", "temperature", "spo2"):
            self.env.cr.execute("""
                SELECT value, date
                FROM health_vital
                WHERE vital_type = %s AND create_uid = %s AND dependent_id IS NOT DISTINCT FROM %s
                ORDER BY date DESC LIMIT 1
            """, (vtype, uid, dep_id))
            row = self.env.cr.dictfetchone()
            if row:
                vitals_snapshot[vtype] = {
                    "value": float(row["value"]),
                    "date": str(row["date"]),
                }

        # --- Weight trend (30 days) ---
        self.env.cr.execute("""
            SELECT date::date AS day, value
            FROM health_vital
            WHERE vital_type = 'weight'
              AND create_uid = %s AND dependent_id IS NOT DISTINCT FROM %s
              AND date >= %s - INTERVAL '30 days'
            ORDER BY date ASC
        """, (uid, dep_id, today))
        weight_trend = [
            {"day": str(r["day"]), "value": float(r["value"])}
            for r in self.env.cr.dictfetchall()
        ]

        # --- Water + calories today ---
        self.env.cr.execute("""
            SELECT
                COALESCE(SUM(value) FILTER (WHERE vital_type = 'water_ml'), 0) AS water,
                COALESCE(SUM(value) FILTER (WHERE vital_type = 'calories'), 0) AS calories
            FROM health_vital
            WHERE date::date = %s AND create_uid = %s AND dependent_id IS NOT DISTINCT FROM %s
        """, (today, uid, dep_id))
        intake = self.env.cr.dictfetchone()

        # --- Upcoming screenings (30 days) ---
        self.env.cr.execute("""
            SELECT id, name, screening_type, next_due, state
            FROM health_screening
            WHERE next_due IS NOT NULL
              AND next_due <= %s + INTERVAL '30 days'
              AND create_uid = %s AND dependent_id IS NOT DISTINCT FROM %s
            ORDER BY next_due ASC
        """, (today, uid, dep_id))
        upcoming_screenings = self.env.cr.dictfetchall()
        for s in upcoming_screenings:
            s["next_due"] = str(s["next_due"]) if s["next_due"] else None

        # --- Overdue items ---
        self.env.cr.execute("""
            SELECT 'screening' AS type, id, name, next_due AS due_date
            FROM health_screening
            WHERE next_due < %s AND create_uid = %s AND dependent_id IS NOT DISTINCT FROM %s
            UNION ALL
            SELECT 'renewal' AS type, id, name, renewal_date AS due_date
            FROM health_medication
            WHERE renewal_date < %s AND state = 'active' AND create_uid = %s AND dependent_id IS NOT DISTINCT FROM %s
            ORDER BY due_date ASC
        """, (today, uid, dep_id, today, uid, dep_id))
        overdue_items = self.env.cr.dictfetchall()
        for item in overdue_items:
            item["due_date"] = str(item["due_date"]) if item["due_date"] else None

        # --- Substance use weekly summary ---
        self.env.cr.execute("""
            SELECT substance_type, SUM(quantity) AS total_qty
            FROM health_substance_log
            WHERE date >= %s - INTERVAL '7 days'
              AND create_uid = %s AND dependent_id IS NOT DISTINCT FROM %s
            GROUP BY substance_type
            ORDER BY substance_type
        """, (today, uid, dep_id))
        substance_weekly = {
            r["substance_type"]: float(r["total_qty"])
            for r in self.env.cr.dictfetchall()
        }

        # --- Active medications count ---
        self.env.cr.execute("""
            SELECT COUNT(*) AS cnt
            FROM health_medication
            WHERE state = 'active' AND create_uid = %s AND dependent_id IS NOT DISTINCT FROM %s
        """, (uid, dep_id,))
        active_meds = self.env.cr.dictfetchone()["cnt"]

        # --- Active conditions count ---
        self.env.cr.execute("""
            SELECT COUNT(*) AS cnt
            FROM health_condition
            WHERE state IN ('active', 'managed') AND create_uid = %s AND dependent_id IS NOT DISTINCT FROM %s
        """, (uid, dep_id,))
        active_conditions = self.env.cr.dictfetchone()["cnt"]

        # --- Daily activity metrics (steps / active minutes) ---
        self.env.cr.execute("""
            SELECT
                COALESCE(SUM(value) FILTER (WHERE vital_type = 'steps'), 0) AS steps,
                COALESCE(SUM(value) FILTER (WHERE vital_type = 'active_minutes'), 0) AS active_minutes
            FROM health_vital
            WHERE date::date = %s AND create_uid = %s AND dependent_id IS NOT DISTINCT FROM %s
        """, (today, uid, dep_id))
        activity = self.env.cr.dictfetchone()

        # --- Latest sleep ---
        self.env.cr.execute("""
            SELECT value, date
            FROM health_vital
            WHERE vital_type = 'sleep_hours' AND create_uid = %s AND dependent_id IS NOT DISTINCT FROM %s
            ORDER BY date DESC LIMIT 1
        """, (uid, dep_id,))
        sleep_row = self.env.cr.dictfetchone()

        # --- Nutrition consumed today ---
        self.env.cr.execute("""
            SELECT
                COALESCE(SUM(calories), 0) AS calories,
                COALESCE(SUM(protein_g), 0) AS protein,
                COALESCE(SUM(carbs_g), 0) AS carbs,
                COALESCE(SUM(fat_g), 0) AS fat
            FROM health_meal_log
            WHERE date = %s AND create_uid = %s AND dependent_id IS NOT DISTINCT FROM %s
        """, (today, uid, dep_id))
        nutrition = self.env.cr.dictfetchone()

        # --- Calories burned via workouts today ---
        self.env.cr.execute("""
            SELECT COALESCE(SUM(calories_burned), 0) AS burned
            FROM health_workout
            WHERE date::date = %s AND create_uid = %s AND dependent_id IS NOT DISTINCT FROM %s
        """, (today, uid, dep_id))
        burned_today = self.env.cr.dictfetchone()["burned"]

        # --- Weekly workout summary (7 days) ---
        self.env.cr.execute("""
            SELECT
                COUNT(*) AS cnt,
                COALESCE(SUM(distance_km), 0) AS distance,
                COALESCE(SUM(duration_min), 0) AS duration
            FROM health_workout
            WHERE date >= %s::date - INTERVAL '7 days' AND create_uid = %s AND dependent_id IS NOT DISTINCT FROM %s
        """, (today, uid, dep_id))
        weekly_workouts = self.env.cr.dictfetchone()

        # --- Active nutrition goal ---
        self.env.cr.execute("""
            SELECT daily_calorie_goal, protein_target_g, carbs_target_g, fat_target_g
            FROM health_nutrition_goal
            WHERE active = TRUE AND create_uid = %s AND dependent_id IS NOT DISTINCT FROM %s
            ORDER BY id DESC LIMIT 1
        """, (uid, dep_id,))
        goal_row = self.env.cr.dictfetchone()

        return {
            "dependent_id": dependant.id or False,
            "dependents": self._bf_personnes_a_charge(),
            "med_compliance": med_compliance,
            "vitals_snapshot": vitals_snapshot,
            "weight_trend": weight_trend,
            "water_today": float(intake["water"]),
            "calories_today": float(intake["calories"]),
            "upcoming_screenings": upcoming_screenings,
            "overdue_items": overdue_items,
            "substance_weekly": substance_weekly,
            "active_meds": active_meds,
            "active_conditions": active_conditions,
            "steps_today": float(activity["steps"]),
            "active_minutes_today": float(activity["active_minutes"]),
            "sleep_hours": float(sleep_row["value"]) if sleep_row else None,
            "nutrition_today": {
                "calories": float(nutrition["calories"]),
                "protein": float(nutrition["protein"]),
                "carbs": float(nutrition["carbs"]),
                "fat": float(nutrition["fat"]),
            },
            "calories_burned_today": float(burned_today),
            "weekly_workouts": {
                "count": weekly_workouts["cnt"] or 0,
                "distance": float(weekly_workouts["distance"]),
                "duration": float(weekly_workouts["duration"]),
            },
            "nutrition_goal": {
                "daily_calorie_goal": goal_row["daily_calorie_goal"] or 0,
                "protein_target_g": goal_row["protein_target_g"] or 0,
                "carbs_target_g": goal_row["carbs_target_g"] or 0,
                "fat_target_g": goal_row["fat_target_g"] or 0,
            } if goal_row else None,
        }

    @api.model
    def _bf_personne_affichee(self, dependent_id):
        """La personne à charge demandée, si l'appelant tient ses fiches.
        Le tableau lit ensuite en SQL brut : le contrôle se fait donc ici."""
        Dependant = self.env["health.dependent"]
        if not dependent_id:
            return Dependant
        dependant = Dependant.browse(int(dependent_id)).exists()
        if not dependant:
            raise AccessError(_("Cette personne n'existe pas."))
        dependant.check_access("read")
        proprietes = dependant.sudo()
        if (self.env.user not in (proprietes.create_uid | proprietes.coparent_id)
                or proprietes.state not in ("suivi", "offert")):
            raise AccessError(_("Vous ne tenez pas les fiches de cette personne."))
        return dependant

    @api.model
    def _bf_personnes_a_charge(self):
        return [
            {"id": d.id, "name": d.name, "passage_du": d.passage_du}
            for d in self.env["health.dependent"].search(
                ["|", ("create_uid", "=", self.env.uid), ("coparent_id", "=", self.env.uid),
                 ("state", "in", ("suivi", "offert"))])
        ]

    @api.model
    def action_open_model(self, model, name, view_type="list"):
        views_map = {
            "list": [(False, "list"), (False, "form")],
            "kanban": [(False, "kanban"), (False, "list"), (False, "form")],
        }
        return {
            "type": "ir.actions.act_window",
            "name": name,
            "res_model": model,
            "views": views_map.get(view_type, [(False, "list"), (False, "form")]),
            "target": "current",
        }
