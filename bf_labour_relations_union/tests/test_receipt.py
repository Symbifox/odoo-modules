from dateutil.relativedelta import relativedelta

from odoo.exceptions import ValidationError
from odoo.tests.common import tagged

from .common import UnionCase


@tagged("post_install", "-at_install")
class TestReceipt(UnionCase):
    """L'écart est le seul chiffre qui compte."""

    def setUp(self):
        super().setUp()
        self.period_start = self.today - relativedelta(days=14)
        self.period_end = self.today

    def _remittance(self, amount, people=1):
        remittance = self.env["bf.labour.dues.remittance"].create({
            "unit_id": self.unit.id,
            "period_start": self.period_start,
            "period_end": self.period_end,
        })
        for index in range(people):
            membership = self._membership("Cotisante %s" % index)
            self.env["bf.labour.dues.remittance.line"].create({
                "remittance_id": remittance.id,
                "membership_id": membership.id,
                "amount": amount / people,
            })
        remittance.invalidate_recordset(["amount_total", "headcount"])
        return remittance

    def _receipt(self, amount, remittance=None, **kw):
        vals = {
            "unit_id": self.unit.id,
            "period_start": self.period_start,
            "period_end": self.period_end,
            "amount_received": amount,
            "remittance_id": remittance.id if remittance else False,
        }
        vals.update(kw)
        return self.env["bf.labour.dues.receipt"].create(vals)

    def test_matching_amounts_reconcile(self):
        remittance = self._remittance(300.0, people=3)
        receipt = self._receipt(300.0, remittance)
        receipt.invalidate_recordset()
        self.assertAlmostEqual(receipt.gap_amount, 0.0, places=2)
        self.assertTrue(receipt.is_reconciled)

    def test_a_shortfall_shows_as_a_negative_gap(self):
        remittance = self._remittance(300.0, people=3)
        receipt = self._receipt(280.0, remittance)
        receipt.invalidate_recordset()
        self.assertAlmostEqual(receipt.gap_amount, -20.0, places=2)
        self.assertFalse(receipt.is_reconciled)

    def test_an_unlinked_receipt_is_never_reconciled(self):
        """Sans remise liée, il n'y a rien à rapprocher.

        Le dire « rapproché » parce que l'écart calcule zéro serait le pire des
        faux verts : l'absence de comparaison se lirait comme une comparaison
        réussie.
        """
        receipt = self._receipt(300.0)
        receipt.invalidate_recordset()
        self.assertFalse(receipt.is_reconciled)
        self.assertAlmostEqual(receipt.amount_declared, 0.0, places=2)

    def test_headcount_gap_is_computed_too(self):
        remittance = self._remittance(300.0, people=3)
        receipt = self._receipt(300.0, remittance, headcount_received=2)
        receipt.invalidate_recordset()
        self.assertEqual(receipt.headcount_declared, 3)
        self.assertEqual(receipt.headcount_gap, -1)

    def test_covered_count_comes_from_the_unit_not_the_remittance(self):
        """Le troisième chiffre : ce que le socle sait de l'unité.

        Un relevé qui compte moins de monde que l'unité n'en couvre mérite une
        question, même si le montant balance.
        """
        remittance = self._remittance(200.0, people=2)
        self._membership("Oubliée du relevé")
        receipt = self._receipt(200.0, remittance, headcount_received=2)
        receipt.invalidate_recordset()
        self.assertTrue(receipt.is_reconciled)
        self.assertEqual(receipt.covered_count, 3)
        self.assertEqual(receipt.headcount_declared, 2)

    def test_a_non_covered_person_is_not_counted_as_covered(self):
        self._membership("Non couverte", covered=False)
        receipt = self._receipt(0.0)
        receipt.invalidate_recordset()
        self.assertEqual(receipt.covered_count, 0)


@tagged("post_install", "-at_install")
class TestGrievanceUnionSide(UnionCase):

    def _grievance(self, **kw):
        vals = {
            "unit_id": self.unit.id,
            "subject": "Grief d'essai",
            "kind": "individual",
        }
        vals.update(kw)
        return self.env["bf.labour.grievance"].create(vals)

    def test_declining_a_grievance_demands_a_written_reason(self):
        """Le devoir de représentation se juge sur ce texte."""
        grievance = self._grievance()
        with self.assertRaises(ValidationError):
            grievance.mandate_state = "declined"

    def test_declining_with_a_reason_is_allowed(self):
        grievance = self._grievance()
        grievance.write({
            "mandate_state": "declined",
            "mandate_reason": "Aucune violation de la convention n'est établie.",
        })
        self.assertEqual(grievance.mandate_state, "declined")

    def test_sending_to_arbitration_dates_the_mandate(self):
        """Le socle change l'état, le greffon date le mandat.

        Un renvoi sans mandat daté laisse le dossier sans la pièce qui prouve
        que la décision a été prise, et quand.
        """
        grievance = self._grievance()
        grievance.action_send_to_arbitration()
        self.assertEqual(grievance.state, "arbitration")
        self.assertEqual(grievance.arbitration_mandate_date, self.today)
        self.assertEqual(grievance.mandate_state, "supported")

    def test_the_union_side_lives_on_the_same_record(self):
        """Pas de second modèle de grief : les champs sont sur celui du socle."""
        grievance = self._grievance()
        grievance.union_file_number = "S-2026-001"
        same = self.env["bf.labour.grievance"].browse(grievance.id)
        self.assertEqual(same.union_file_number, "S-2026-001")
        self.assertEqual(same.subject, "Grief d'essai")
