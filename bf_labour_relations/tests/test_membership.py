from dateutil.relativedelta import relativedelta

from odoo.exceptions import ValidationError
from odoo.tests.common import tagged

from .common import LabourCase


@tagged("post_install", "-at_install")
class TestMembership(LabourCase):
    """L'appartenance, et surtout les deux états qu'on aurait pu confondre."""

    def test_covered_without_being_a_member(self):
        """🔴 Le cas qui justifie deux champs plutôt qu'un.

        L'article 47 du Code du travail impose la retenue à tout salarié de
        l'unité, membre ou non. Une personne couverte qui n'a pas adhéré cotise
        quand même ; ce qui lui manque, c'est le droit de vote.
        """
        person = self._employee("Couverte non membre")
        line = self._membership(person, is_member=False)
        self.assertTrue(line.covered)
        self.assertFalse(line.is_member)
        person.invalidate_recordset(["labour_covered", "labour_is_member"])
        self.assertTrue(person.labour_covered)
        self.assertFalse(person.labour_is_member)

    def test_member_counts_and_covered_counts_differ(self):
        self._membership(self._employee("A"))
        self._membership(self._employee("B"), is_member=False)
        self._membership(self._employee("C"), covered=False, is_member=True)
        self.unit.invalidate_recordset(["covered_count", "member_count"])
        self.assertEqual(self.unit.covered_count, 2)
        self.assertEqual(self.unit.member_count, 2)

    def test_seniority_differing_from_entry_requires_a_reason(self):
        person = self._employee("Ancienneté reconnue")
        with self.assertRaises(ValidationError):
            self._membership(
                person,
                date_start=self.today - relativedelta(years=1),
                seniority_date=self.today - relativedelta(years=8),
            )

    def test_seniority_with_a_reason_is_accepted(self):
        person = self._employee("Ancienneté justifiée")
        line = self._membership(
            person,
            date_start=self.today - relativedelta(years=1),
            seniority_date=self.today - relativedelta(years=8),
            seniority_reason="Ancienneté reconnue au transfert, lettre d'entente 3.",
        )
        self.assertEqual(line.seniority_date, self.today - relativedelta(years=8))
        self.assertAlmostEqual(line.seniority_years, 8.0, places=1)

    def test_seniority_is_not_the_contract_date(self):
        """L'ancienneté du module ne vient pas de hr_contract.

        Elle est saisie, elle se corrige avec un motif, et elle survit à une
        interruption. Un calcul dérivé du contrat rendrait la mauvaise réponse
        exactement dans les cas qui comptent.
        """
        person = self._employee("Rappelée")
        line = self._membership(
            person,
            date_start=self.today - relativedelta(months=2),
            seniority_date=self.today - relativedelta(years=12),
            seniority_reason="Rappel après mise à pied, ancienneté conservée.",
        )
        person.invalidate_recordset(["labour_seniority_date"])
        self.assertEqual(person.labour_seniority_date, line.seniority_date)

    def test_membership_cannot_cross_companies(self):
        """Un transfert ne crée pas une appartenance à cheval sur deux sociétés."""
        outsider = self._employee("Autre établissement", company=self.company_free)
        with self.assertRaises(ValidationError):
            self._membership(outsider)

    def test_departure_does_not_delete_the_line(self):
        person = self._employee("Partie")
        line = self._membership(person, date_end=self.today - relativedelta(days=1))
        self.assertFalse(line.is_current)
        self.assertTrue(line.exists())
        person.invalidate_recordset(["labour_covered", "labour_seniority_date"])
        self.assertFalse(person.labour_covered)
        self.assertFalse(person.labour_seniority_date)

    def test_leaving_before_entering_is_refused(self):
        person = self._employee("Dates inversées")
        with self.assertRaises(ValidationError):
            self._membership(person, date_end=self.today - relativedelta(years=5))
