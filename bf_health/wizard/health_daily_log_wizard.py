from odoo import api, fields, models

from ..models.health_meal_log import MEAL_TYPES
from ..models.gen_portees import LIBELLE_SAISIE, PORTEE_SAISIE
from ..models.health_mood import NIVEAUX
from ..models.parent_guard import garder_parents
from ..models.health_workout import ACTIVITY_TYPES


class HealthDailyLogWizard(models.TransientModel):
    _name = "health.daily.log.wizard"
    _description = "Saisie rapide quotidienne"
    # L'assistant porte l'humeur du jour avant qu'elle soit
    # enregistrée. Il entre dans la portée du verrou de Gen : sur le canal API,
    # personne ne relit un assistant de saisie.
    _gen_scope = PORTEE_SAISIE
    _gen_scope_label = LIBELLE_SAISIE

    date = fields.Date(
        string="Date", required=True, default=fields.Date.context_today
    )

    # Vitals
    weight = fields.Float(string="Poids (kg)", digits=(10, 1))
    bp_systolic = fields.Float(string="PA systolique (mmHg)")
    bp_diastolic = fields.Float(string="PA diastolique (mmHg)")
    heart_rate = fields.Float(string="Fréquence cardiaque (bpm)")
    water_ml = fields.Float(string="Eau (mL)")
    calories = fields.Float(string="Calories (kcal)")

    # Daily activity metrics (Fitbit-style)
    steps = fields.Float(string="Pas")
    active_minutes = fields.Float(string="Minutes actives")
    resting_hr = fields.Float(string="FC au repos (bpm)")
    sleep_hours = fields.Float(string="Sommeil (h)", digits=(10, 1))

    # Workout (Strava-style)
    workout_type = fields.Selection(ACTIVITY_TYPES, string="Type de séance")
    workout_duration = fields.Float(string="Durée (min)", digits=(10, 1))
    workout_distance = fields.Float(string="Distance (km)", digits=(10, 2))
    workout_calories = fields.Float(string="Calories brûlées")

    # Meal log lines (MyFitnessPal-style)
    meal_line_ids = fields.One2many(
        "health.daily.log.wizard.meal.line",
        "wizard_id",
        string="Repas",
    )

    # Substance
    substance_type = fields.Selection(
        [
            ("alcohol", "Alcool"),
            ("cannabis", "Cannabis"),
            ("tobacco", "Tabac"),
            ("caffeine", "Caféine"),
        ],
        string="Substance",
    )
    substance_qty = fields.Float(string="Quantité")
    substance_unit = fields.Selection(
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
    substance_context = fields.Char(string="Contexte")

    # Humeur
    mood_level = fields.Selection(NIVEAUX, string="Humeur")
    mood_activity_ids = fields.Many2many(
        "health.mood.activity", "health_daily_log_wizard_mood_activity_rel",
        "wizard_id", "activity_id", string="Activités")
    mood_note = fields.Text(string="Note sur l'humeur")

    # Med log lines
    med_line_ids = fields.One2many(
        "health.daily.log.wizard.med.line",
        "wizard_id",
        string="Médicaments",
    )

    def _bf_garder_lignes(self, valeurs):
        """Les lignes neuves de l'assistant ne visent pas une fiche
        parente illisible (médicament ou aliment d'une autre personne)."""
        for champ, modele, parents in (("med_line_ids", "health.daily.log.wizard.med.line", ("medication_id",)),
                                       ("meal_line_ids", "health.daily.log.wizard.meal.line", ("food_id",))):
            for commande in (valeurs or {}).get(champ) or []:
                if isinstance(commande, (list, tuple)) and len(commande) >= 3 and isinstance(commande[2], dict):
                    garder_parents(self.env[modele], commande[2], parents)

    def onchange(self, values, field_names, fields_spec):
        self._bf_garder_lignes(values)
        garder_parents(self, values or {}, ("mood_activity_ids",))
        return super().onchange(values, field_names, fields_spec)

    @api.model
    def default_get(self, fields_list):
        """``default_med_line_ids`` du contexte."""
        valeurs = super().default_get(fields_list)
        self._bf_garder_lignes(valeurs)
        garder_parents(self, valeurs, ("mood_activity_ids",))
        return valeurs

    @api.constrains("mood_activity_ids")
    def _check_activites_humeur_lisibles(self):
        """On ne coche qu'une activité qu'on peut lire."""
        if self.env.su:
            return
        for rec in self:
            rec.mood_activity_ids.check_access("read")

    @api.onchange("date")
    def _onchange_date(self):
        """Populate med lines from active medications."""
        MedLog = self.env["health.medication.log"]
        meds = self.env["health.medication"].search([("state", "=", "active")])
        lines = []
        for med in meds:
            # Check if already logged
            existing = MedLog.search([
                ("medication_id", "=", med.id),
                ("date", "=", self.date),
            ])
            existing_slots = {e.time_slot for e in existing}

            slot_map = {
                "daily": ["morning"],
                "twice_daily": ["morning", "evening"],
                "three_daily": ["morning", "noon", "evening"],
                "weekly": ["morning"],
                "biweekly": ["morning"],
                "monthly": ["morning"],
                "as_needed": [],
            }
            slots = slot_map.get(med.frequency, ["morning"])
            for slot in slots:
                taken = slot in existing_slots and any(
                    e.taken for e in existing if e.time_slot == slot
                )
                lines.append((0, 0, {
                    "medication_id": med.id,
                    "time_slot": slot,
                    "taken": taken,
                }))
        self.med_line_ids = lines

    def action_confirm(self):
        self.ensure_one()
        Vital = self.env["health.vital"]
        MedLog = self.env["health.medication.log"]
        SubLog = self.env["health.substance.log"]

        # Create vitals
        vital_map = {
            "weight": self.weight,
            "bp_systolic": self.bp_systolic,
            "bp_diastolic": self.bp_diastolic,
            "heart_rate": self.heart_rate,
            "water_ml": self.water_ml,
            "calories": self.calories,
            "steps": self.steps,
            "active_minutes": self.active_minutes,
            "resting_hr": self.resting_hr,
            "sleep_hours": self.sleep_hours,
        }
        for vtype, val in vital_map.items():
            if val:
                Vital.create({
                    "date": fields.Datetime.to_datetime(self.date),
                    "vital_type": vtype,
                    "value": val,
                })

        # Create/update med logs
        for line in self.med_line_ids:
            existing = MedLog.search([
                ("medication_id", "=", line.medication_id.id),
                ("date", "=", self.date),
                ("time_slot", "=", line.time_slot),
            ], limit=1)
            if existing:
                existing.write({"taken": line.taken})
            else:
                MedLog.create({
                    "medication_id": line.medication_id.id,
                    "date": self.date,
                    "time_slot": line.time_slot,
                    "taken": line.taken,
                })

        # Create substance log
        if self.substance_type and self.substance_qty:
            SubLog.create({
                "date": self.date,
                "substance_type": self.substance_type,
                "quantity": self.substance_qty,
                "quantity_unit": self.substance_unit,
                "context": self.substance_context,
            })

        # Create workout
        if self.workout_type and (self.workout_duration or self.workout_distance):
            type_labels = dict(ACTIVITY_TYPES)
            self.env["health.workout"].create({
                "name": type_labels.get(self.workout_type, "Séance"),
                "activity_type": self.workout_type,
                "date": fields.Datetime.to_datetime(self.date),
                "duration_min": self.workout_duration,
                "distance_km": self.workout_distance,
                "calories_burned": int(self.workout_calories or 0),
            })

        # Humeur
        if self.mood_level:
            self.env["health.mood.entry"].create({
                "date": self.date,
                "level": self.mood_level,
                "activity_ids": [(6, 0, self.mood_activity_ids.ids)],
                "note": self.mood_note or False,
            })

        # Create meal log entries
        MealLog = self.env["health.meal.log"]
        for line in self.meal_line_ids:
            if line.food_id:
                MealLog.create({
                    "date": self.date,
                    "meal_type": line.meal_type,
                    "food_id": line.food_id.id,
                    "servings": line.servings or 1.0,
                })

        return {"type": "ir.actions.act_window_close"}


class HealthDailyLogWizardMedLine(models.TransientModel):
    _name = "health.daily.log.wizard.med.line"
    _gen_scope = PORTEE_SAISIE
    _gen_scope_label = LIBELLE_SAISIE
    _inherit = ["bf.health.parent.guard"]
    _bf_champs_parents = ("medication_id",)
    _description = "Ligne de médicament du wizard"

    wizard_id = fields.Many2one(
        "health.daily.log.wizard", ondelete="cascade"
    )
    medication_id = fields.Many2one(
        "health.medication", string="Médicament", required=True
    )
    time_slot = fields.Selection(
        [
            ("morning", "Matin"),
            ("noon", "Midi"),
            ("evening", "Soir"),
            ("night", "Nuit"),
            ("as_needed", "Au besoin"),
        ],
        string="Moment",
    )
    taken = fields.Boolean(string="Pris")

    @api.constrains("medication_id")
    def _check_medicament_lisible(self):
        """Une ligne d'assistant ne vise qu'un médicament lisible
        (sinon son nom se relit par le Many2one, calculé en superutilisateur)."""
        self._bf_verifier_parents()


class HealthDailyLogWizardMealLine(models.TransientModel):
    _name = "health.daily.log.wizard.meal.line"
    _gen_scope = PORTEE_SAISIE
    _gen_scope_label = LIBELLE_SAISIE
    _inherit = ["bf.health.parent.guard"]
    _bf_champs_parents = ("food_id",)
    _description = "Ligne de repas du wizard"

    wizard_id = fields.Many2one(
        "health.daily.log.wizard", ondelete="cascade"
    )
    meal_type = fields.Selection(
        MEAL_TYPES, string="Repas", required=True, default="breakfast"
    )
    food_id = fields.Many2one(
        "health.food", string="Aliment", required=True
    )
    servings = fields.Float(string="Portions", default=1.0, digits=(10, 2))

    @api.constrains("food_id")
    def _check_aliment_lisible(self):
        """Une ligne d'assistant ne vise qu'un aliment lisible."""
        self._bf_verifier_parents()
