from datetime import datetime, time

from dateutil.relativedelta import relativedelta

from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import AssemblyCase


@tagged("post_install", "-at_install", "bf_membership_assembly")
class TestLocalDay(AssemblyCase):
    """🔴 Le délai d'avis se compte sur le jour de l'organisme, pas sur le jour UTC."""

    def setUp(self):
        super().setUp()
        self.company.partner_id.tz = "America/Toronto"

    def _evening(self, days):
        """21 h 30 (ou 20 h 30 l'hiver) à Montréal : 1 h 30 le LENDEMAIN en UTC."""
        return datetime.combine(self._day(days=days) + relativedelta(days=1), time(1, 30))

    def test_evening_meeting_falls_on_the_local_day(self):
        assembly = self._assembly(date=self._evening(10))
        self.assertEqual(assembly._meeting_day(), self._day(days=10))
        assembly.action_convene()
        self.assertEqual(assembly.notice_days, 10)

    def test_evening_meeting_nine_local_days_away_is_refused(self):
        """Compté sur le jour UTC, cet avis aurait dix jours et passerait."""
        with self.assertRaises(UserError):
            self._assembly(date=self._evening(9)).action_convene()
