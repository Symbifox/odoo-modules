from dateutil.relativedelta import relativedelta

from odoo.exceptions import ValidationError
from odoo.tests.common import tagged

from .common import UnionCase


@tagged("post_install", "-at_install")
class TestDelegate(UnionCase):

    def setUp(self):
        super().setUp()
        self.membership = self._membership("Déléguée")
        self.delegate = self.env["bf.labour.delegate"].create({
            "membership_id": self.membership.id,
            "role": "delegate",
            "date_start": self.today - relativedelta(months=6),
            "hours_granted": 40.0,
        })

    def _release(self, hours, state="taken", **kw):
        vals = {
            "delegate_id": self.delegate.id,
            "date": self.today,
            "hours": hours,
            "reason": "Rencontre de grief",
            "state": state,
        }
        vals.update(kw)
        return self.env["bf.labour.delegate.release"].create(vals)

    def test_only_taken_hours_are_deducted(self):
        self._release(10.0, state="taken")
        self._release(5.0, state="requested")
        self.delegate.invalidate_recordset()
        self.assertAlmostEqual(self.delegate.hours_taken, 10.0, places=2)
        self.assertAlmostEqual(self.delegate.hours_left, 30.0, places=2)

    def test_a_refused_release_counts_for_nothing_but_stays_on_file(self):
        """🔴 C'est le refus qu'on ressort en grief.

        Le compter en heures fausserait le solde ; le supprimer effacerait la
        preuve.
        """
        refused = self._release(
            8.0, state="refused",
            refusal_reason="Effectif insuffisant sur le quart.",
        )
        self.delegate.invalidate_recordset()
        self.assertAlmostEqual(self.delegate.hours_taken, 0.0, places=2)
        self.assertTrue(refused.exists())

    def test_refusing_without_a_reason_is_blocked(self):
        with self.assertRaises(ValidationError):
            self._release(8.0, state="refused")

    def test_going_over_the_bank_is_flagged_not_blocked(self):
        """Dépasser la banque arrive, et c'est justement ce qu'il faut voir."""
        self._release(50.0)
        self.delegate.invalidate_recordset()
        self.assertTrue(self.delegate.is_over)
        self.assertAlmostEqual(self.delegate.hours_left, -10.0, places=2)

    def test_zero_hours_is_refused(self):
        with self.assertRaises(ValidationError):
            self._release(0.0)

    def test_mandate_dates_must_be_ordered(self):
        with self.assertRaises(ValidationError):
            self.env["bf.labour.delegate"].create({
                "membership_id": self.membership.id,
                "date_start": self.today,
                "date_end": self.today - relativedelta(days=1),
            })

    def test_a_finished_mandate_is_not_current(self):
        self.delegate.date_end = self.today - relativedelta(days=1)
        self.delegate.invalidate_recordset(["is_current"])
        self.assertFalse(self.delegate.is_current)
