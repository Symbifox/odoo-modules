from datetime import timedelta

from dateutil.relativedelta import relativedelta

from odoo.exceptions import ValidationError
from odoo.tests.common import tagged

from .common import LabourCase


@tagged("post_install", "-at_install")
class TestGrievance(LabourCase):
    """Le grief, et surtout le calendrier, qui est là où il se perd."""

    def _step(self, grievance, **kw):
        vals = {
            "grievance_id": grievance.id,
            "name": "Première étape",
            "date_start": self.today,
            "delay_days": 10,
        }
        vals.update(kw)
        return self.env["bf.labour.grievance.step"].create(vals)

    def test_grievance_gets_a_number(self):
        grievance = self._grievance()
        self.assertTrue(grievance.name.startswith("GR/"))

    def test_deadline_is_computed_from_the_conventional_delay(self):
        grievance = self._grievance()
        step = self._step(grievance, date_start=self.today, delay_days=15)
        self.assertEqual(step.deadline, self.today + timedelta(days=15))

    def test_no_delay_means_no_deadline_not_today(self):
        """Zéro jour veut dire « aucun délai imposé », pas « échéance aujourd'hui ».

        Confondre les deux ferait sonner l'alarme sur toutes les étapes qu'une
        convention laisse ouvertes.
        """
        grievance = self._grievance()
        step = self._step(grievance, delay_days=0)
        self.assertFalse(step.deadline)
        self.assertFalse(step.is_late)

    def test_next_deadline_is_the_first_one_still_open(self):
        grievance = self._grievance()
        self._step(grievance, name="Deuxième", date_start=self.today, delay_days=30)
        first = self._step(grievance, name="Première", date_start=self.today, delay_days=5)
        grievance.invalidate_recordset(["next_deadline"])
        self.assertEqual(grievance.next_deadline, first.deadline)

    def test_a_closed_step_stops_carrying_the_deadline(self):
        grievance = self._grievance()
        first = self._step(grievance, name="Première", delay_days=5)
        second = self._step(grievance, name="Deuxième", delay_days=30)
        first.action_done()
        grievance.invalidate_recordset(["next_deadline"])
        self.assertEqual(grievance.next_deadline, second.deadline)

    def test_late_when_the_deadline_has_passed(self):
        grievance = self._grievance()
        self._step(grievance, date_start=self.today - relativedelta(days=40), delay_days=10)
        grievance.action_file()
        grievance.invalidate_recordset(["next_deadline", "is_late", "days_to_deadline"])
        self.assertTrue(grievance.is_late)
        self.assertLess(grievance.days_to_deadline, 0)

    def test_a_settled_grievance_is_never_late(self):
        """Un dossier fermé ne doit plus apparaître en retard.

        Sinon la liste des griefs en souffrance se remplit de dossiers réglés
        et cesse d'être lue.
        """
        grievance = self._grievance()
        self._step(grievance, date_start=self.today - relativedelta(days=40), delay_days=10)
        grievance.action_settle()
        grievance.invalidate_recordset(["is_late"])
        self.assertFalse(grievance.is_late)
        self.assertEqual(grievance.state, "settled")
        self.assertEqual(grievance.date_closed, self.today)

    def test_settling_cancels_the_pending_steps(self):
        grievance = self._grievance()
        step = self._step(grievance)
        grievance.action_settle()
        self.assertEqual(step.state, "cancelled")

    def test_employer_grievance_flips_the_claimant_side(self):
        """Le vocabulaire du socle reste neutre : un grief patronal existe."""
        grievance = self._grievance(kind="employer")
        self.assertEqual(grievance.claimant_side, "employer")
        union_side = self._grievance(kind="union")
        self.assertEqual(union_side.claimant_side, "union")

    def test_cannot_file_before_the_events(self):
        with self.assertRaises(ValidationError):
            self._grievance(
                date_event=self.today,
                date_filed=self.today - relativedelta(days=1),
            )

    def test_agreement_must_belong_to_the_same_unit(self):
        other_unit = self.env["bf.labour.unit"].create({
            "name": "Autre unité",
            "company_id": self.company_union.id,
            "union_id": self.union.id,
        })
        stranger = self.env["bf.labour.agreement"].create({
            "unit_id": other_unit.id,
            "date_start": self.today - relativedelta(years=1),
            "date_end": self.today + relativedelta(years=1),
        })
        with self.assertRaises(ValidationError):
            self._grievance(agreement_id=stranger.id)
