"""Second parent d'une personne à charge.

Montage : A, le parent qui tient les fiches (titulaire) ; C, le second parent ; B,
un autre membre ; T, le compte de l'ado ; Blue Fox, administratrice hors du groupe
santé. Chaque essai sème au nom de la personne, vide les caches, puis joue le geste.

Données inventées seulement.
"""
from odoo import fields
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import new_test_user, tagged
from odoo.tests.common import TransactionCase

from ..models.health_dependent import MODELES_A_CHARGE


@tagged("post_install", "-at_install", "isolation_menage", "second_parent")
class TestSecondParent(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        groupes = "base.group_user,bf_health.group_health_user"
        tz = "America/Montreal"
        cls.a = new_test_user(cls.env, login="second_titulaire_a", groups=groupes, name="Titulaire A",
                              email="titulaire.a@exemple.invalid", tz=tz)
        cls.c = new_test_user(cls.env, login="second_parent_c", groups=groupes, name="Second C",
                              email="second.c@exemple.invalid", tz=tz)
        cls.b = new_test_user(cls.env, login="second_membre_b", groups=groupes, name="Membre B", tz=tz)
        cls.t = new_test_user(cls.env, login="second_ado_t", groups=groupes, name="Ado T", tz=tz)
        cls.bf = new_test_user(cls.env, login="second_blue_fox", groups="base.group_user,base.group_system",
                               name="Blue Fox", tz=tz)
        cls.today = fields.Date.context_today(cls.env["health.dependent"].with_user(cls.a))
        cls.midi = fields.Datetime.to_datetime(f"{cls.today} 16:00:00")

    def _en(self, user, model):
        self.env.invalidate_all()
        return self.env[model].with_user(user)

    def _enfant(self, second=True, ans=9):
        vals = {"name": "Enfant fictif", "birth_month": str(self.today.month),
                "birth_year": self.today.year - ans, "consentement_parent": True}
        if second:
            vals["coparent_id"] = self.c.id
        return self._en(self.a, "health.dependent").create(vals)

    def _semer(self, user, dependant):
        E = lambda m: self._en(user, m)  # noqa: E731
        pour = {"dependent_id": dependant.id}
        med = E("health.medication").create({"name": f"Médicament de {user.login}", "state": "active", **pour})
        cond = E("health.condition").create({"name": f"Condition de {user.login}", **pour})
        return {
            "health.medication": med,
            "health.medication.log": E("health.medication.log").create(
                {"medication_id": med.id, "date": self.today, "time_slot": "morning", "taken": True}),
            "health.vital": E("health.vital").create({"vital_type": "weight", "value": 30, "date": self.midi, **pour}),
            "health.lab.test": E("health.lab.test").create({"name": "Analyse fictive", **pour}),
            "health.condition": cond,
            "health.symptom.log": E("health.symptom.log").create({"name": "Symptôme fictif", "condition_id": cond.id}),
            "health.screening": E("health.screening").create({"name": "Examen fictif", **pour}),
            "health.substance.log": E("health.substance.log").create(
                {"substance_type": "caffeine", "quantity": 1, **pour}),
            "health.reduction.step": E("health.reduction.step").create({
                "code": "E1", "name": "Étape fictive", "phase": "2_reduction", "substance_type": "caffeine", **pour}),
            "health.workout": E("health.workout").create({"name": "Natation fictive", **pour}),
            "health.nutrition.goal": E("health.nutrition.goal").create({"name": "Objectif fictif", **pour}),
        }

    # ------------------------------------------------------------------ accès
    def test_second_parent_keeps_the_childs_records(self):
        dependant = self._enfant()
        fiches = self._semer(self.a, dependant)
        for modele, rec in fiches.items():
            with self.subTest(modele=modele):
                self.assertTrue(self._en(self.c, modele).search([("id", "=", rec.id)]), "C lit")
                self._en(self.c, modele).browse(rec.id).write({"notes": "Note de C"} if "notes" in rec._fields
                                                               else {})
                self.assertFalse(self._en(self.b, modele).search([("id", "=", rec.id)]), "B ne lit rien")

    def test_record_created_by_second_parent_belongs_to_the_holder(self):
        dependant = self._enfant()
        fiches = self._semer(self.c, dependant)
        for modele, rec in fiches.items():
            with self.subTest(modele=modele):
                self.assertEqual(rec.sudo().create_uid, self.a)
                self.assertTrue(self._en(self.a, modele).search([("id", "=", rec.id)]), "A lit")
                self.assertTrue(self._en(self.c, modele).search([("id", "=", rec.id)]), "C lit encore")

    def test_dashboard_of_the_child_for_the_second_parent(self):
        dependant = self._enfant()
        self._semer(self.c, dependant)
        tableau = self._en(self.c, "health.dashboard").get_dashboard_data(dependant.id)
        self.assertEqual(tableau["med_compliance"]["total"], 1)
        with self.assertRaises(AccessError):
            self._en(self.b, "health.dashboard").get_dashboard_data(dependant.id)

    def test_meal_with_the_second_parents_food(self):
        dependant = self._enfant()
        aliment = self._en(self.c, "health.food").create({"name": "Aliment de C", "calories": 111})
        repas = self._en(self.c, "health.meal.log").create({"food_id": aliment.id, "dependent_id": dependant.id})
        copie = repas.sudo().food_id
        self.assertNotEqual(copie, aliment, "Le repas pointe vers une copie.")
        self.assertEqual(copie.create_uid, self.a)
        self.assertEqual(self._en(self.a, "health.meal.log").browse(repas.id).food_id.name, "Aliment de C")
        self.assertEqual(self._en(self.c, "health.food").browse(copie.id).name, "Aliment de C")
        self.assertFalse(self._en(self.b, "health.food").search([("id", "=", copie.id)]))

    def test_second_parent_cannot_take_a_record_out_of_the_child(self):
        dependant = self._enfant()
        vital = self._semer(self.c, dependant)["health.vital"]
        with self.assertRaises(AccessError):
            self._en(self.c, "health.vital").browse(vital.id).write({"dependent_id": False})

    def test_activities_on_the_childs_records(self):
        """Le second parent peut se poser un rappel sur une fiche de l'enfant ; personne
        d'autre ne le reçoit (il lirait son résumé et le nom de la fiche)."""
        dependant = self._enfant()
        med = self._semer(self.a, dependant)["health.medication"]
        type_todo = self.env.ref("mail.mail_activity_data_todo")
        self._en(self.c, "health.medication").browse(med.id).activity_schedule(
            act_type_xmlid="mail.mail_activity_data_todo", summary="Rappel de C", user_id=self.c.id)
        self.assertTrue(med.sudo().activity_ids.filtered(lambda a: a.user_id == self.c))
        with self.assertRaises(ValidationError):
            self.env["mail.activity"].create({
                "res_model_id": self.env["ir.model"]._get_id("health.medication"), "res_id": med.id,
                "activity_type_id": type_todo.id, "summary": "Pour B", "user_id": self.b.id})

    # ------------------------------------------------------------------ qui nomme qui
    def test_only_the_holder_names_the_second_parent(self):
        dependant = self._enfant(second=False)
        with self.assertRaises(AccessError):
            self._en(self.b, "health.dependent").browse(dependant.id).write({"coparent_id": self.b.id})
        self._en(self.a, "health.dependent").browse(dependant.id).write({"coparent_id": self.c.id})
        with self.assertRaises(AccessError):
            self._en(self.c, "health.dependent").browse(dependant.id).write({"coparent_id": self.b.id})

    def test_second_parent_constraints(self):
        dependant = self._enfant(second=False)
        for mauvais in (self.a, self.bf):
            with self.subTest(compte=mauvais.login), self.assertRaises(ValidationError):
                self._en(self.a, "health.dependent").browse(dependant.id).write({"coparent_id": mauvais.id})

    def test_second_parent_steps_back_and_loses_everything(self):
        dependant = self._enfant()
        fiches = self._semer(self.c, dependant)
        med = fiches["health.medication"]
        message = med.with_user(self.c).message_post(body="Message de C", message_type="comment",
                                                      subtype_xmlid="mail.mt_note")
        self._en(self.c, "health.dependent").browse(dependant.id).write({"coparent_id": False})
        for modele, rec in fiches.items():
            with self.subTest(modele=modele):
                self.assertFalse(self._en(self.c, modele).search([("id", "=", rec.id)]))
        self.assertFalse(self._en(self.c, "health.dependent").search([("id", "=", dependant.id)]))
        with self.assertRaises(AccessError):
            self._en(self.c, "mail.message").browse(message.id).read(["body"])
        self.assertNotIn(self.c.partner_id, med.sudo().message_partner_ids)

    def test_second_parent_erases_the_records(self):
        dependant = self._enfant()
        self._semer(self.a, dependant)
        self._en(self.c, "health.dependent").browse(dependant.id).action_effacer()
        self.assertFalse(dependant.exists())

    # ------------------------------------------------------------------ échange et passage
    def test_change_parents_hands_the_records_over(self):
        dependant = self._enfant()
        fiches = self._semer(self.a, dependant)
        dependant.sudo()._bf_changer_parents(self.c, self.a)
        self.assertEqual(dependant.sudo().create_uid, self.c)
        self.assertEqual(dependant.sudo().coparent_id, self.a)
        for modele, rec in fiches.items():
            with self.subTest(modele=modele):
                self.assertEqual(rec.sudo().create_uid, self.c)
                self.assertTrue(self._en(self.a, modele).search([("id", "=", rec.id)]), "A, second parent")

    def test_after_the_move_each_parent_gets_only_what_is_shared(self):
        dependant = self._enfant()
        fiches = self._semer(self.a, dependant)
        dependant.sudo().write({"birth_year": self.today.year - 14, "birth_month": str(self.today.month)})
        self.env.invalidate_all()
        self._en(self.bf, "health.dependent").browse(dependant.id).write({"ado_id": self.t.id})
        self._en(self.bf, "health.dependent").browse(dependant.id).action_proposer_passage()
        self._en(self.t, "health.dependent").browse(dependant.id).action_recevoir()
        vital, med = fiches["health.vital"], fiches["health.medication"]
        for parent in (self.a, self.c):
            self.assertFalse(self._en(parent, "health.vital").search([("id", "=", vital.id)]))
        self._en(self.t, "health.dependent").browse(dependant.id).write({"partage2_vital": True})
        self.assertTrue(self._en(self.c, "health.vital").search([("id", "=", vital.id)]), "C reçoit les signes vitaux")
        self.assertFalse(self._en(self.a, "health.vital").search([("id", "=", vital.id)]), "A n'a rien reçu")
        self.assertFalse(self._en(self.c, "health.medication").search([("id", "=", med.id)]))

    def test_models_with_dependent_are_all_covered(self):
        couverts = {r.model_id.model for r in self.env["ir.rule"].sudo().search([("name", "like", "le second parent")])}
        self.assertEqual(set(MODELES_A_CHARGE) - couverts, set())
