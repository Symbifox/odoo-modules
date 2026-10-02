from datetime import date

from odoo.exceptions import ValidationError
from odoo.tests import tagged

from .common import MembershipCase


@tagged("post_install", "-at_install", "bf_membership")
class TestPeriods(MembershipCase):

    def test_fixed_period_mid_year_ends_with_the_fiscal_year(self):
        """Une adhésion prise en octobre finit le 31 mars suivant, sans prorata de dates."""
        end = self.type_person._period_end(date(2026, 10, 15))
        self.assertEqual(end, date(2027, 3, 31))

    def test_fixed_period_before_fiscal_start_belongs_to_previous_year(self):
        self.assertEqual(self.type_person._period_end(date(2027, 2, 1)), date(2027, 3, 31))
        self.assertEqual(self.type_person._period_end(date(2027, 4, 1)), date(2028, 3, 31))

    def test_fixed_period_label_spans_two_years(self):
        self.assertEqual(self.type_person._period_label(date(2026, 4, 1), date(2027, 3, 31)), "2026-2027")

    def test_calendar_year_label_is_one_year(self):
        calendar_type = self.type_person.copy({"code": "CAL", "period_start_month": "1"})
        end = calendar_type._period_end(date(2026, 5, 3))
        self.assertEqual(end, date(2026, 12, 31))
        self.assertEqual(calendar_type._period_label(date(2026, 5, 3), end), "2026")

    def test_rolling_period_counts_months_from_start(self):
        self.assertEqual(self.type_org._period_end(date(2026, 10, 1)), date(2027, 9, 30))
        self.assertEqual(self.type_org._period_end(date(2026, 1, 31)), date(2027, 1, 30))

    def test_lifetime_has_no_end(self):
        self.assertFalse(self.type_honorary._period_end(date(2026, 10, 1)))
        membership = self._membership(self.bruno, self.type_honorary)
        self.assertFalse(membership.date_end)

    def test_february_29_fiscal_start_falls_back(self):
        leap = self.type_person.copy({"code": "LEAP", "period_start_month": "2", "period_start_day": 29})
        self.assertEqual(leap._fiscal_start(2027), date(2027, 2, 28))
        self.assertEqual(leap._fiscal_start(2028), date(2028, 2, 29))

    def test_invalid_fiscal_day_refused(self):
        with self.assertRaises(ValidationError):
            self.type_person.copy({"code": "BAD", "period_start_month": "4", "period_start_day": 31})

    def test_end_date_is_editable(self):
        """Une prolongation décidée par le conseil s'écrit à la main et tient."""
        membership = self._membership(self.alice, date_start=date(2026, 4, 1))
        membership.date_end = date(2027, 6, 30)
        membership.amount = 50.0
        self.assertEqual(membership.date_end, date(2027, 6, 30))
        self.assertEqual(membership.amount, 50.0)
