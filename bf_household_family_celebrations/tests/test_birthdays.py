"""Les anniversaires des enfants du foyer, posés par le cron de Célébrations."""
from datetime import date, timedelta

from odoo.tests import TransactionCase, new_test_user, tagged

HOUSEHOLD = "bf_household_base.group_household_user"


@tagged("post_install", "-at_install")
class TestBirthdays(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.camille = new_test_user(cls.env, login="fc-camille", name="Camille FC", groups=HOUSEHOLD)
        dans_dix_jours = date.today() + timedelta(days=10)
        cls.lea = cls.env["bf.household.child"].with_user(cls.camille).create({
            "name": "Léa", "birth_year": dans_dix_jours.year - 7, "birth_month": str(dans_dix_jours.month),
            "birth_day": dans_dix_jours.day,
        })
        cls.date_attendue = dans_dix_jours
        cls.Occasion = cls.env["bf.celebration.occasion"]

    def _occasions(self, enfant):
        return self.Occasion.sudo().search([("partner_id", "=", enfant.partner_id.id), ("occasion_type", "=", "birthday")])

    def test_birthday_is_posted_once(self):
        self.Occasion._cron_generer()
        occasions = self._occasions(self.lea)
        self.assertEqual(len(occasions), 1)
        self.assertEqual(occasions.date, self.date_attendue)
        self.assertEqual(occasions.organizer_id, self.camille)
        self.assertFalse(occasions.years, "Aucun âge.")
        self.Occasion._cron_generer()
        self.assertEqual(len(self._occasions(self.lea)), 1)

    def test_no_birthday_without_the_day_or_when_turned_off(self):
        sans_jour = self.env["bf.household.child"].with_user(self.camille).create(
            {"name": "Noé", "birth_year": 2020, "birth_month": str(self.date_attendue.month)})
        self.lea.with_user(self.camille).celebrate_birthday = False
        self.Occasion._cron_generer()
        self.assertFalse(self._occasions(self.lea))
        self.assertFalse(self._occasions(sans_jour))
