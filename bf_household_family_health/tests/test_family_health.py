"""Un enfant du foyer est une seule fiche, dans la famille et dans Healthy Fox."""
from datetime import date

from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, new_test_user, tagged

HOUSEHOLD = "bf_household_base.group_household_user"
# Le profil du foyer donne Healthy Fox à toute la maisonnée ; la famille seule non, d'où le
# groupe santé en toutes lettres (sans lui, l'essai ne tourne que là où le profil est installé).
MEMBRE = HOUSEHOLD + ",bf_health.group_health_user"


@tagged("post_install", "-at_install")
class TestFamilyHealth(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.camille = new_test_user(cls.env, login="fh-camille", name="Camille FH", email="c@fh.test", groups=MEMBRE)
        cls.alex = new_test_user(cls.env, login="fh-alex", name="Alex FH", email="a@fh.test", groups=MEMBRE)
        cls.sam = new_test_user(cls.env, login="fh-sam", name="Sam FH", email="s@fh.test", groups=MEMBRE)
        cls.annee = date.today().year - 8

    def _en(self, user, model):
        self.env.invalidate_all()
        return self.env[model].with_user(user)

    def _suivre(self, user, **vals):
        valeurs = {"name": "Léa", "birth_month": "3", "birth_year": self.annee, "consentement_parent": True}
        valeurs.update(vals)
        return self._en(user, "health.dependent").create(valeurs)

    def test_dependent_creates_the_household_child(self):
        dependant = self._suivre(self.camille)
        enfant = dependant.sudo().household_child_id
        self.assertTrue(enfant)
        self.assertEqual((enfant.name, enfant.birth_year, enfant.birth_month), ("Léa", self.annee, "3"))
        self.assertEqual(enfant.primary_parent_id, self.camille)

    def test_dependent_for_an_existing_child_takes_its_parents(self):
        enfant = self._en(self.camille, "bf.household.child").create(
            {"name": "Noé", "birth_year": self.annee, "birth_month": "5", "second_parent_id": self.alex.id})
        dependant = self._suivre(self.camille, household_child_id=enfant.id, name="Autre nom")
        self.assertEqual(dependant.sudo().name, "Noé")
        self.assertEqual(dependant.sudo().coparent_id, self.alex)
        vital = self._en(self.camille, "health.vital").create(
            {"vital_type": "weight", "value": 25, "dependent_id": dependant.id})
        self.assertTrue(self._en(self.alex, "health.vital").search([("id", "=", vital.id)]))
        self.assertFalse(self._en(self.sam, "health.vital").search([("id", "=", vital.id)]))

    def test_only_the_primary_parent_starts_health_tracking(self):
        enfant = self._en(self.camille, "bf.household.child").create(
            {"name": "Noé", "birth_year": self.annee, "birth_month": "5", "second_parent_id": self.alex.id})
        with self.assertRaises(AccessError):
            self._suivre(self.alex, household_child_id=enfant.id)
        with self.assertRaises(AccessError):
            self._suivre(self.sam, household_child_id=enfant.id)

    def test_second_parent_follows_the_family(self):
        dependant = self._suivre(self.camille)
        enfant = dependant.sudo().household_child_id
        vital = self._en(self.camille, "health.vital").create(
            {"vital_type": "weight", "value": 25, "dependent_id": dependant.id})
        self._en(self.camille, "bf.household.child").browse(enfant.id).write({"second_parent_id": self.alex.id})
        self.assertEqual(dependant.sudo().coparent_id, self.alex)
        self.assertTrue(self._en(self.alex, "health.vital").search([("id", "=", vital.id)]))
        self._en(self.alex, "bf.household.child").browse(enfant.id).write({"second_parent_id": False})
        self.assertFalse(dependant.sudo().coparent_id)
        self.assertFalse(self._en(self.alex, "health.vital").search([("id", "=", vital.id)]))

    def test_health_second_parent_comes_back_to_the_family(self):
        dependant = self._suivre(self.camille)
        self._en(self.camille, "health.dependent").browse(dependant.id).write({"coparent_id": self.alex.id})
        self.assertEqual(dependant.sudo().household_child_id.second_parent_id, self.alex)

    def test_swap_parents_hands_the_health_records_over(self):
        dependant = self._suivre(self.camille, coparent_id=self.alex.id)
        vital = self._en(self.alex, "health.vital").create(
            {"vital_type": "weight", "value": 25, "dependent_id": dependant.id})
        self.assertEqual(vital.sudo().create_uid, self.camille)
        enfant = dependant.sudo().household_child_id
        self._en(self.camille, "bf.household.child").browse(enfant.id).action_swap_parents()
        self.assertEqual(dependant.sudo().create_uid, self.alex)
        self.assertEqual(dependant.sudo().coparent_id, self.camille)
        self.assertEqual(vital.sudo().create_uid, self.alex)
        self.assertTrue(self._en(self.camille, "health.vital").search([("id", "=", vital.id)]))

    def test_primary_parent_leaves_and_the_other_holds_the_records(self):
        dependant = self._suivre(self.camille, coparent_id=self.alex.id)
        vital = self._en(self.camille, "health.vital").create(
            {"vital_type": "weight", "value": 25, "dependent_id": dependant.id})
        self._en(self.camille, "bf.household.leave").create({"confirm": True}).action_leave()
        self.assertEqual(dependant.sudo().create_uid, self.alex)
        self.assertFalse(dependant.sudo().coparent_id)
        self.assertTrue(self._en(self.alex, "health.vital").search([("id", "=", vital.id)]))

    def test_parents_cannot_move_the_birth_healthy_fox_follows(self):
        dependant = self._suivre(self.camille, coparent_id=self.alex.id)
        enfant = dependant.sudo().household_child_id
        with self.assertRaises(AccessError):
            self._en(self.camille, "bf.household.child").browse(enfant.id).write({"birth_year": self.annee - 3})
        self._en(self.camille, "bf.household.child").browse(enfant.id).write({"birth_day": 12})
        self.assertEqual(enfant.birth_day, 12)

    def test_non_household_account_keeps_a_lone_dependent(self):
        hors = new_test_user(self.env, login="fh-hors", name="Hors du foyer",
                             groups="base.group_user,bf_health.group_health_user")
        dependant = self._suivre(hors)
        self.assertFalse(dependant.sudo().household_child_id)

    def test_emergency_card_shows_health_only_when_ticked(self):
        self._en(self.sam, "health.medication").create({"name": "Ventolin", "dosage": "100 mcg", "state": "active"})
        self._en(self.sam, "health.medication").create({"name": "Ancien", "state": "discontinued"})
        self._en(self.sam, "health.condition").create({"name": "Asthme", "state": "managed"})
        self._en(self.sam, "health.vital").create({"vital_type": "weight", "value": 77})
        carte = self._en(self.sam, "bf.household.emergency.card").create({"allergies": "Arachides"})
        lue = self._en(self.camille, "bf.household.emergency.card").browse(carte.id)
        self.assertFalse(lue.health_summary)
        carte.with_user(self.sam).include_health = True
        lue = self._en(self.camille, "bf.household.emergency.card").browse(carte.id)
        self.assertIn("Ventolin (100 mcg)", lue.health_summary)
        self.assertIn("Asthme", lue.health_summary)
        self.assertNotIn("Ancien", lue.health_summary)
        self.assertNotIn("77", lue.health_summary)
        with self.assertRaises(AccessError):
            self._en(self.camille, "bf.household.emergency.card").browse(carte.id).write({"include_health": False})

    def test_child_card_shows_the_childs_health(self):
        dependant = self._suivre(self.camille)
        self._en(self.camille, "health.medication").create(
            {"name": "Sirop", "state": "active", "dependent_id": dependant.id})
        self._en(self.camille, "health.medication").create({"name": "Pilule de Camille", "state": "active"})
        carte = self._en(self.camille, "bf.household.emergency.card").create(
            {"child_id": dependant.sudo().household_child_id.id, "include_health": True})
        resume = self._en(self.sam, "bf.household.emergency.card").browse(carte.id).health_summary
        self.assertIn("Sirop", resume)
        self.assertNotIn("Camille", resume)
