from dateutil.relativedelta import relativedelta

from odoo.exceptions import ValidationError
from odoo.tests.common import tagged

from .common import LabourCase


@tagged("post_install", "-at_install")
class TestUnit(LabourCase):

    def test_certified_unit_needs_its_date(self):
        """Une unité accréditée porte la date de son accréditation."""
        with self.assertRaises(ValidationError):
            self.env["bf.labour.unit"].create({
                "name": "Sans date",
                "company_id": self.company_union.id,
                "union_id": self.union.id,
                "state": "certified",
            })

    def test_current_agreement_is_the_latest_started(self):
        newer = self.env["bf.labour.agreement"].create({
            "unit_id": self.unit.id,
            "date_start": self.today - relativedelta(months=1),
            "date_end": self.today + relativedelta(years=3),
            "state": "in_force",
        })
        self.unit.invalidate_recordset(["current_agreement_id"])
        self.assertEqual(self.unit.current_agreement_id, newer)

    def test_a_future_agreement_is_not_current(self):
        """Une convention signée d'avance ne devient pas la convention du jour."""
        self.env["bf.labour.agreement"].create({
            "unit_id": self.unit.id,
            "date_start": self.today + relativedelta(months=6),
            "date_end": self.today + relativedelta(years=4),
            "state": "in_force",
        })
        self.unit.invalidate_recordset(["current_agreement_id"])
        self.assertEqual(self.unit.current_agreement_id, self.agreement)

    def test_putting_in_force_supersedes_the_previous_one(self):
        successor = self.env["bf.labour.agreement"].create({
            "unit_id": self.unit.id,
            "date_start": self.today,
            "date_end": self.today + relativedelta(years=3),
        })
        successor.action_put_in_force()
        self.assertEqual(self.agreement.state, "superseded")
        self.assertEqual(self.agreement.superseded_by_id, successor)
        self.assertEqual(successor.state, "in_force")

    def test_an_expired_agreement_stays_in_force(self):
        """Échue ne veut pas dire annulée.

        Les conditions continuent de s'appliquer jusqu'au remplacement. C'est
        l'échéance qui dit qu'il faut négocier, pas l'état.
        """
        self.agreement.date_end = self.today - relativedelta(days=1)
        self.agreement.invalidate_recordset(["is_expired", "days_to_expiry"])
        self.assertTrue(self.agreement.is_expired)
        self.assertEqual(self.agreement.state, "in_force")
        self.assertLess(self.agreement.days_to_expiry, 0)

    def test_agreement_dates_must_be_ordered(self):
        with self.assertRaises(ValidationError):
            self.env["bf.labour.agreement"].create({
                "unit_id": self.unit.id,
                "date_start": self.today,
                "date_end": self.today - relativedelta(days=1),
            })

    def test_a_company_without_a_unit_still_works(self):
        """Le parc mixte est le cas normal.

        Une personne d'une société sans accréditation doit se lire sans erreur
        et sans être marquée couverte.
        """
        person = self._employee("Sans syndicat", company=self.company_free)
        self.assertFalse(person.labour_unit_id)
        self.assertFalse(person.labour_covered)
        self.assertFalse(person.labour_is_member)
        self.assertFalse(person.labour_seniority_date)
        self.assertEqual(person.labour_membership_count, 0)

    def test_counts_ignore_people_who_left(self):
        stays = self._employee("Reste")
        leaves = self._employee("Partie")
        self._membership(stays)
        self._membership(leaves, date_end=self.today - relativedelta(days=1))
        self.unit.invalidate_recordset(["covered_count", "member_count"])
        self.assertEqual(self.unit.covered_count, 1)
        self.assertEqual(self.unit.member_count, 1)
