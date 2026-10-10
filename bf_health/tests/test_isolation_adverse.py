"""L'assistant de saisie et onchange() ne rendent plus le nom ni les valeurs d'une fiche parente illisible. Données inventées."""
from odoo.exceptions import AccessError
from odoo.tests import new_test_user, tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install", "isolation_menage")
class TestIsolationSanteAdverse(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        g = "base.group_user,bf_health.group_health_user"
        cls.a = new_test_user(cls.env, login="adv_sante_a", groups=g)
        cls.b = new_test_user(cls.env, login="adv_sante_b", groups=g)
        cls.med = cls.env["health.medication"].with_user(cls.a).create({"name": "SECRET-MED-A"})
        cls.food = cls.env["health.food"].with_user(cls.a).create({"name": "SECRET-FOOD-A", "calories": 777})
        cls.cond = cls.env["health.condition"].with_user(cls.a).create({"name": "SECRET-COND-A"})

    def _b(self, model):
        self.env.invalidate_all()
        return self.env[model].with_user(self.b)

    def test_ligne_d_assistant_vers_le_medicament_de_a(self):
        with self.assertRaises(AccessError):
            self._b("health.daily.log.wizard").create(
                {"med_line_ids": [(0, 0, {"medication_id": self.med.id, "time_slot": "morning"})]})

    def test_ligne_d_assistant_vers_l_aliment_de_a(self):
        with self.assertRaises(AccessError):
            self._b("health.daily.log.wizard").create(
                {"meal_line_ids": [(0, 0, {"food_id": self.food.id, "meal_type": "lunch"})]})

    def test_onchange_journal_de_repas(self):
        with self.assertRaises(AccessError):
            self._b("health.meal.log").onchange(
                {"food_id": self.food.id, "servings": 1.0}, ["food_id"],
                {"food_id": {}, "calories": {}, "servings": {}})

    def test_onchange_journal_de_medicament(self):
        with self.assertRaises(AccessError):
            self._b("health.medication.log").onchange(
                {"medication_id": self.med.id}, ["medication_id"],
                {"medication_id": {"fields": {"display_name": {}}}, "medication_name": {}})

    def test_onchange_journal_de_symptome(self):
        with self.assertRaises(AccessError):
            self._b("health.symptom.log").onchange(
                {"condition_id": self.cond.id}, ["condition_id"], {"condition_id": {}})

    def test_onchange_de_l_assistant(self):
        with self.assertRaises(AccessError):
            self._b("health.daily.log.wizard").onchange(
                {"med_line_ids": [(0, 0, {"medication_id": self.med.id})]}, ["med_line_ids"],
                {"med_line_ids": {"fields": {"medication_id": {"fields": {"display_name": {}}}}}})

    def test_defauts_du_contexte(self):
        """2e relecture : ``default_medication_id`` & cie au premier onchange."""
        cas = (("health.medication.log", "default_medication_id", self.med.id,
                {"medication_id": {"fields": {"display_name": {}}}, "medication_name": {}}),
               ("health.meal.log", "default_food_id", self.food.id,
                {"food_id": {"fields": {"display_name": {}}}, "calories": {}, "servings": {}}),
               ("health.symptom.log", "default_condition_id", self.cond.id, {"condition_id": {}}))
        for modele, cle, rid, spec in cas:
            with self.subTest(modele=modele):
                with self.assertRaises(AccessError):
                    self._b(modele).with_context(**{cle: rid}).onchange({}, [], spec)
        with self.assertRaises(AccessError):
            self._b("health.daily.log.wizard").with_context(
                default_med_line_ids=[(0, 0, {"medication_id": self.med.id})]).onchange(
                {}, [], {"med_line_ids": {"fields": {"medication_id": {"fields": {"display_name": {}}}}}})
        with self.assertRaises(AccessError):
            self._b("health.medication.log").onchange(
                {"medication_id": str(self.med.id)}, ["medication_id"], {"medication_name": {}})

    def test_defauts_du_contexte_en_dict(self):
        """3e relecture : un défaut donné en dict (``{'id': 317}``, qui passe en JSON)
        devient un ``NewId(origin=317)`` dans ``default_get`` ; la garde doit remonter à
        l'origine au lieu de ne voir aucun id entier. La forme exacte de la sonde est
        refusée ; pour les autres formes, l'exigence est qu'AUCUN secret de A ne revienne
        (refus, ou valeur non résolue)."""
        cas = (("health.medication.log", "default_medication_id", self.med.id,
                {"medication_id": {"fields": {"display_name": {}}}, "medication_name": {}}),
               ("health.meal.log", "default_food_id", self.food.id,
                {"food_id": {"fields": {"display_name": {}}}, "calories": {}}),
               ("health.symptom.log", "default_condition_id", self.cond.id,
                {"condition_id": {"fields": {"display_name": {}}}}))
        for modele, cle, rid, spec in cas:
            with self.subTest(modele=modele, forme="dict entier"):
                with self.assertRaises(AccessError):
                    self._b(modele).with_context(**{cle: {"id": rid}}).onchange({}, [], spec)
            for forme in ({"id": str(rid)}, [rid, "x"]):
                with self.subTest(modele=modele, forme=forme):
                    try:
                        r = self._b(modele).with_context(**{cle: forme}).onchange({}, [], spec)
                    except (AccessError, ValueError, TypeError):
                        continue
                    texte = repr(r)
                    for secret in ("SECRET-MED-A", "SECRET-FOOD-A", "SECRET-COND-A", "777"):
                        self.assertNotIn(secret, texte)

    def test_defaut_en_dict_de_sa_propre_fiche_nest_pas_refuse(self):
        med_b = self._b("health.medication").create({"name": "Med de B"})
        self._b("health.medication.log").with_context(
            default_medication_id={"id": med_b.id}).onchange({}, [], {"medication_name": {}})

    def test_contre_epreuve_a_garde_ses_gestes(self):
        med_b = self._b("health.medication").create({"name": "Med de B"})
        r = self._b("health.medication.log").onchange(
            {"medication_id": med_b.id}, ["medication_id"], {"medication_name": {}})
        self.assertEqual(r["value"].get("medication_name"), "Med de B")
        w = self._b("health.daily.log.wizard").create(
            {"med_line_ids": [(0, 0, {"medication_id": med_b.id, "time_slot": "morning"})]})
        self.assertEqual(w.med_line_ids.medication_id, med_b)
