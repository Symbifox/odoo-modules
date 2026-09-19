from dateutil.relativedelta import relativedelta

from odoo.exceptions import ValidationError
from odoo.tests.common import tagged

from .common import EmployerCase


@tagged("post_install", "-at_install")
class TestObligation(EmployerCase):

    def _obligation(self, **kw):
        vals = {
            "name": "Transmettre la liste d'ancienneté",
            "unit_id": self.unit.id,
            "date_due": self.today + relativedelta(days=30),
            "reminder_days": 14,
        }
        vals.update(kw)
        return self.env["bf.labour.obligation"].create(vals)

    def test_reminder_is_posted_when_the_window_opens(self):
        obligation = self._obligation(date_due=self.today + relativedelta(days=10))
        posted = self.env["bf.labour.obligation"]._cron_post_reminders()
        self.assertIn(obligation, posted)
        self.assertTrue(obligation.activity_ids)
        self.assertTrue(obligation.reminder_posted)

    def test_reminder_is_not_posted_too_early(self):
        obligation = self._obligation(date_due=self.today + relativedelta(days=60))
        posted = self.env["bf.labour.obligation"]._cron_post_reminders()
        self.assertNotIn(obligation, posted)
        self.assertFalse(obligation.activity_ids)

    def test_a_cancelled_reminder_does_not_come_back(self):
        """⚠️ Le filtre porte sur le drapeau, pas sur l'existence d'une activité.

        Sinon une activité qu'on a annulée à la main revient à chaque passage
        du traitement, et le rappel devient du bruit qu'on cesse de lire.
        """
        obligation = self._obligation(date_due=self.today + relativedelta(days=5))
        self.env["bf.labour.obligation"]._cron_post_reminders()
        obligation.activity_ids.unlink()
        posted = self.env["bf.labour.obligation"]._cron_post_reminders()
        self.assertNotIn(obligation, posted)

    def test_zero_days_means_no_reminder(self):
        obligation = self._obligation(reminder_days=0, date_due=self.today)
        posted = self.env["bf.labour.obligation"]._cron_post_reminders()
        self.assertNotIn(obligation, posted)

    def test_a_negative_reminder_is_refused(self):
        with self.assertRaises(ValidationError):
            self._obligation(reminder_days=-5)

    def test_completing_a_recurring_obligation_creates_the_next_one(self):
        """La reconduction CRÉE le suivant au lieu de déplacer celui-ci.

        Déplacer effacerait l'historique de conformité au premier passage, et
        c'est précisément cet historique qu'un syndicat ressort.
        """
        obligation = self._obligation(recurrence="quarterly")
        followers = obligation.action_done()
        self.assertEqual(obligation.state, "done")
        self.assertEqual(obligation.date_done, self.today)
        self.assertEqual(len(followers), 1)
        self.assertEqual(followers.state, "pending")
        self.assertEqual(
            followers.date_due, obligation.date_due + relativedelta(months=3),
        )
        self.assertFalse(followers.reminder_posted)

    def test_a_one_off_obligation_does_not_recur(self):
        obligation = self._obligation(recurrence="none")
        followers = obligation.action_done()
        self.assertFalse(followers)

    def test_lateness_is_measured_against_today(self):
        late = self._obligation(date_due=self.today - relativedelta(days=3))
        self.assertTrue(late.is_late)
        self.assertEqual(late.days_to_due, -3)
        late.action_done()
        late.invalidate_recordset(["is_late"])
        self.assertFalse(late.is_late)
