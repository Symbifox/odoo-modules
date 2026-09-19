from dateutil.relativedelta import relativedelta

from odoo.exceptions import UserError
from odoo.tests.common import tagged

from .common import EmployerCase


@tagged("post_install", "-at_install")
class TestRemittance(EmployerCase):

    def setUp(self):
        super().setUp()
        self.person = self._member("Cotisante", 4)
        self.remittance = self.env["bf.labour.dues.remittance"].create({
            "unit_id": self.unit.id,
            "period_start": self.today - relativedelta(days=14),
            "period_end": self.today,
        })
        self.remittance.action_fill_from_unit()
        self.line = self.remittance.line_ids

    def test_the_rule_is_applied_to_the_entered_base(self):
        self.line.base_amount = 1000.0
        self.line.invalidate_recordset(["computed_amount"])
        # 2 % de 1000 plus 1 $ fixe.
        self.assertAlmostEqual(self.line.computed_amount, 21.0, places=2)

    def test_a_gap_is_shown_not_silently_fixed(self):
        """Un écart n'est pas une erreur : il se justifie, ou il se corrige.

        L'effacer en silence supprimerait exactement ce qu'une vérification
        syndicale cherche.
        """
        self.line.base_amount = 1000.0
        self.line.amount = 15.0
        self.line.invalidate_recordset(["computed_amount", "has_gap"])
        self.assertTrue(self.line.has_gap)
        self.remittance.invalidate_recordset(["gap_amount", "gap_line_count"])
        self.assertEqual(self.remittance.gap_line_count, 1)
        self.assertAlmostEqual(self.remittance.gap_amount, 6.0, places=2)
        # Le montant déclaré n'a pas bougé.
        self.assertAlmostEqual(self.line.amount, 15.0, places=2)

    def test_applying_the_rule_is_an_explicit_gesture(self):
        self.line.base_amount = 1000.0
        self.line.amount = 15.0
        self.remittance.action_apply_rule()
        self.assertAlmostEqual(self.line.amount, 21.0, places=2)
        self.line.invalidate_recordset(["has_gap"])
        self.assertFalse(self.line.has_gap)

    def test_a_declared_remittance_is_not_recomputed(self):
        self.line.base_amount = 1000.0
        self.remittance.action_declare()
        with self.assertRaises(UserError):
            self.remittance.action_apply_rule()

    def test_the_rule_in_force_for_the_period_is_used_not_the_latest(self):
        """🔴 Un taux qui change laisse deux règles datées.

        Prendre la plus récente donnerait le mauvais montant sur toute période
        antérieure au changement, et ce genre d'erreur ne se voit qu'au
        moment de la vérification.
        """
        self.env["bf.labour.dues.rule"].create({
            "agreement_id": self.agreement.id,
            "date_start": self.today + relativedelta(days=1),
            "rate_percent": 5.0,
            "amount_fixed": 0.0,
            "basis": "gross",
        })
        self.line.base_amount = 1000.0
        self.line.invalidate_recordset(["computed_amount"])
        self.assertAlmostEqual(self.line.computed_amount, 21.0, places=2)

    def test_no_base_means_no_computed_amount(self):
        self.line.base_amount = 0.0
        self.line.invalidate_recordset(["computed_amount", "has_gap"])
        self.assertAlmostEqual(self.line.computed_amount, 0.0, places=2)
        self.assertFalse(self.line.has_gap)

    def test_base_total_adds_up(self):
        other = self._member("Deuxième", 2)
        self.remittance.action_fill_from_unit()
        self.remittance.line_ids.base_amount = 500.0
        self.remittance.invalidate_recordset(["base_total", "computed_total"])
        self.assertEqual(len(self.remittance.line_ids), 2)
        self.assertAlmostEqual(self.remittance.base_total, 1000.0, places=2)
        self.assertAlmostEqual(self.remittance.computed_total, 22.0, places=2)
        self.assertTrue(other)
