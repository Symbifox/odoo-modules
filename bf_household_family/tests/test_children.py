"""Les enfants du foyer : un ou deux parents nommés.

Tout le foyer lit le prénom et l'anniversaire. Seuls les parents écrivent. Le
parent principal est celui qui crée la fiche ; il ne change que par l'échange.
"""
from datetime import date

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged

from .common import FamilyCase


@tagged("post_install", "-at_install")
class TestChildren(FamilyCase):

    def test_creator_is_primary_parent(self):
        enfant = self.as_user(self.sam, "bf.household.child").create(
            {"name": "Noé", "birth_year": 2020, "birth_month": "6", "primary_parent_id": self.alex.id})
        self.assertEqual(enfant.primary_parent_id, self.sam, "On ne crée pas l'enfant d'un autre.")

    def test_household_reads_first_name_and_birthday(self):
        lu = self.as_user(self.sam, "bf.household.child").browse(self.lea.id)
        self.assertEqual(lu.name, "Léa")
        self.assertEqual(lu.birthday_label, "14 March")

    def test_only_parents_write(self):
        with self.assertRaises(AccessError):
            self.as_user(self.sam, "bf.household.child").browse(self.lea.id).write({"name": "Autre"})
        self.as_user(self.alex, "bf.household.child").browse(self.lea.id).write({"name": "Léa-Rose"})
        self.assertEqual(self.lea.name, "Léa-Rose")
        self.assertEqual(self.lea.partner_id.name, "Léa-Rose", "Le contact suit le prénom.")

    def test_only_parents_delete(self):
        with self.assertRaises(AccessError):
            self.as_user(self.sam, "bf.household.child").browse(self.lea.id).unlink()

    def test_primary_parent_never_changes_by_write(self):
        with self.assertRaises(AccessError):
            self.as_user(self.alex, "bf.household.child").browse(self.lea.id).write(
                {"primary_parent_id": self.alex.id})
        with self.assertRaises(AccessError):
            self.as_user(self.camille, "bf.household.child").browse(self.lea.id).write(
                {"primary_parent_id": self.sam.id})

    def test_second_parent_named_by_primary_only(self):
        with self.assertRaises(AccessError):
            self.as_user(self.alex, "bf.household.child").browse(self.lea.id).write(
                {"second_parent_id": self.sam.id})
        self.as_user(self.camille, "bf.household.child").browse(self.lea.id).write(
            {"second_parent_id": self.sam.id})
        self.assertEqual(self.lea.second_parent_id, self.sam)

    def test_second_parent_can_step_back(self):
        self.as_user(self.alex, "bf.household.child").browse(self.lea.id).write({"second_parent_id": False})
        self.assertFalse(self.lea.second_parent_id)

    def test_parent_is_an_adult_member(self):
        with self.assertRaises(ValidationError):
            self.as_user(self.camille, "bf.household.child").browse(self.lea.id).write(
                {"second_parent_id": self.teo.id})
        with self.assertRaises(ValidationError):
            self.as_user(self.camille, "bf.household.child").browse(self.lea.id).write(
                {"second_parent_id": self.admin.id})

    def test_swap_parents(self):
        with self.assertRaises(AccessError):
            self.as_user(self.alex, "bf.household.child").browse(self.lea.id).action_swap_parents()
        self.as_user(self.camille, "bf.household.child").browse(self.lea.id).action_swap_parents()
        self.assertEqual(self.lea.primary_parent_id, self.alex)
        self.assertEqual(self.lea.second_parent_id, self.camille)

    def test_swap_needs_a_second_parent(self):
        self.lea.with_user(self.camille).second_parent_id = False
        with self.assertRaises(UserError):
            self.as_user(self.camille, "bf.household.child").browse(self.lea.id).action_swap_parents()

    def test_birth_cannot_be_in_the_future(self):
        with self.assertRaises(ValidationError):
            self.as_user(self.camille, "bf.household.child").create(
                {"name": "Futur", "birth_year": date.today().year + 1, "birth_month": "1"})
        with self.assertRaises(ValidationError):
            self.as_user(self.camille, "bf.household.child").create(
                {"name": "Trente", "birth_year": 2020, "birth_month": "2", "birth_day": 30})

    def test_next_birthday_handles_february_29(self):
        enfant = self.as_user(self.camille, "bf.household.child").create(
            {"name": "Bissextile", "birth_year": 2020, "birth_month": "2", "birth_day": 29})
        self.assertEqual(enfant.next_birthday(date(2027, 1, 10)), date(2027, 2, 28))
        self.assertEqual(enfant.next_birthday(date(2028, 1, 10)), date(2028, 2, 29))

    def test_contact_is_created_for_the_calendar(self):
        self.assertTrue(self.lea.partner_id)
        evenement = self.as_user(self.sam, "calendar.event").create({
            "name": "Piscine", "start": "2026-11-01 15:00:00", "stop": "2026-11-01 16:00:00",
            "partner_ids": [(4, self.lea.partner_id.id)],
        })
        self.assertIn(self.lea.partner_id, evenement.partner_ids)

    def test_own_account_is_a_teen_account(self):
        with self.assertRaises(ValidationError):
            self.as_user(self.camille, "bf.household.child").browse(self.lea.id).write({"user_id": self.sam.id})
        self.as_user(self.camille, "bf.household.child").browse(self.lea.id).write({"user_id": self.teo.id})
        self.assertEqual(self.lea.user_id, self.teo)
