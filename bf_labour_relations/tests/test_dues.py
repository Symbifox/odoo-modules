from dateutil.relativedelta import relativedelta

from odoo.exceptions import ValidationError
from odoo.tests.common import tagged

from .common import LabourCase


@tagged("post_install", "-at_install")
class TestDues(LabourCase):

    def _rule(self, **kw):
        vals = {
            "agreement_id": self.agreement.id,
            "rate_percent": 1.75,
            "amount_fixed": 0.50,
            "basis": "gross",
            "period": "pay",
        }
        vals.update(kw)
        return self.env["bf.labour.dues.rule"].create(vals)

    def test_both_forms_add_up(self):
        """🔴 « 1,75 % plus 0,50 $ par paie » est la formulation ordinaire.

        Un modèle à choix exclusif obligerait à mentir dès la première
        convention saisie.
        """
        rule = self._rule()
        self.assertAlmostEqual(rule.amount_for(1000.0), 18.0, places=2)

    def test_percentage_alone_is_valid(self):
        rule = self._rule(amount_fixed=0.0)
        self.assertAlmostEqual(rule.amount_for(1000.0), 17.5, places=2)

    def test_fixed_alone_is_valid(self):
        rule = self._rule(rate_percent=0.0, amount_fixed=12.0)
        self.assertAlmostEqual(rule.amount_for(1000.0), 12.0, places=2)

    def test_a_rule_that_charges_nothing_is_refused(self):
        with self.assertRaises(ValidationError):
            self._rule(rate_percent=0.0, amount_fixed=0.0)

    def test_floor_lifts_a_small_amount(self):
        rule = self._rule(rate_percent=1.0, amount_fixed=0.0, floor_amount=15.0)
        self.assertAlmostEqual(rule.amount_for(100.0), 15.0, places=2)

    def test_cap_trims_a_large_amount(self):
        rule = self._rule(rate_percent=1.0, amount_fixed=0.0, cap_amount=25.0)
        self.assertAlmostEqual(rule.amount_for(10000.0), 25.0, places=2)

    def test_zero_cap_means_no_cap(self):
        """Un plafond à zéro n'est pas un plafond de zéro.

        Sans cette distinction, toute règle qui ne borne rien retiendrait zéro.
        """
        rule = self._rule(rate_percent=1.0, amount_fixed=0.0, cap_amount=0.0)
        self.assertAlmostEqual(rule.amount_for(10000.0), 100.0, places=2)

    def test_floor_above_cap_is_refused(self):
        with self.assertRaises(ValidationError):
            self._rule(floor_amount=50.0, cap_amount=10.0)

    def test_remittance_lines_follow_coverage_not_membership(self):
        """🔴 Le bouton prend les personnes COUVERTES, pas les membres.

        C'est la couverture qui fait cotiser. Prendre les membres laisserait
        hors de la remise exactement les gens que l'article 47 vise.
        """
        covered_member = self._employee("Couverte et membre")
        covered_only = self._employee("Couverte non membre")
        member_only = self._employee("Membre non couverte")
        self._membership(covered_member)
        self._membership(covered_only, is_member=False)
        self._membership(member_only, covered=False, is_member=True)

        remittance = self.env["bf.labour.dues.remittance"].create({
            "unit_id": self.unit.id,
            "period_start": self.today - relativedelta(days=14),
            "period_end": self.today,
        })
        remittance.action_fill_from_unit()

        people = remittance.line_ids.mapped("employee_id")
        self.assertIn(covered_member, people)
        self.assertIn(covered_only, people)
        self.assertNotIn(member_only, people)
        self.assertEqual(remittance.headcount, 2)

    def test_filling_twice_does_not_duplicate(self):
        self._membership(self._employee("Une seule fois"))
        remittance = self.env["bf.labour.dues.remittance"].create({
            "unit_id": self.unit.id,
            "period_start": self.today - relativedelta(days=14),
            "period_end": self.today,
        })
        remittance.action_fill_from_unit()
        remittance.action_fill_from_unit()
        self.assertEqual(remittance.headcount, 1)

    def test_total_follows_the_lines(self):
        self._membership(self._employee("Cotisante"))
        remittance = self.env["bf.labour.dues.remittance"].create({
            "unit_id": self.unit.id,
            "period_start": self.today - relativedelta(days=14),
            "period_end": self.today,
        })
        remittance.action_fill_from_unit()
        remittance.line_ids.amount = 18.0
        remittance.invalidate_recordset(["amount_total"])
        self.assertAlmostEqual(remittance.amount_total, 18.0, places=2)
        remittance.action_declare()
        self.assertEqual(remittance.state, "declared")
        remittance.action_mark_remitted()
        self.assertEqual(remittance.state, "remitted")
        self.assertEqual(remittance.date_remitted, self.today)

    def test_period_must_be_ordered(self):
        with self.assertRaises(ValidationError):
            self.env["bf.labour.dues.remittance"].create({
                "unit_id": self.unit.id,
                "period_start": self.today,
                "period_end": self.today - relativedelta(days=1),
            })
