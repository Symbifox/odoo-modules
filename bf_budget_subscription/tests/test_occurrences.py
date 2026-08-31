from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.tests import tagged

from .common import BfBudgetSubscriptionCommon


@tagged("post_install", "-at_install")
class TestOccurrences(BfBudgetSubscriptionCommon):
    """Le générateur d'échéances est une aide PURE : il se teste sans budget."""

    def test_monthly_gives_twelve_over_a_year(self):
        sub = self._make_subscription("monthly", 100.0)
        dates = sub._budget_occurrences(self.date_start, self.date_end)
        self.assertEqual(len(dates), 12)
        self.assertEqual(dates[0][0], self.date_start)
        self.assertEqual(dates[0][1], 100.0)

    def test_each_cycle_gives_its_own_count(self):
        for cycle, expected in (
            ("monthly", 12), ("quarterly", 4), ("semi_annual", 2),
            ("annual", 1), ("biennial", 1), ("triennial", 1),
        ):
            sub = self._make_subscription(cycle, 100.0)
            self.assertEqual(
                len(sub._budget_occurrences(self.date_start, self.date_end)), expected,
                "cycle %s" % cycle,
            )

    def test_on_demand_has_no_calendar_at_all(self):
        """🔴 Un abonnement à la demande dépense sans échéancier.

        Il ne doit pas produire d'échéance fictive, et il ne doit pas non plus
        passer pour un engagement nul en silence : le drapeau existe pour ça.
        """
        sub = self._make_subscription("on_demand", 50.0)
        self.assertFalse(sub.budget_has_calendar)
        self.assertEqual(sub._budget_occurrences(self.date_start, self.date_end), [])

    def test_one_time_gives_exactly_one(self):
        sub = self._make_subscription("one_time", 900.0)
        occurrences = sub._budget_occurrences(self.date_start, self.date_end)
        self.assertEqual(len(occurrences), 1)
        self.assertEqual(occurrences[0][1], 900.0)

    def test_nothing_before_the_start_date(self):
        later = self.date_start + relativedelta(months=6)
        sub = self._make_subscription("monthly", 100.0, start=later)
        occurrences = sub._budget_occurrences(self.date_start, self.date_end)
        self.assertEqual(len(occurrences), 6)  # juillet à décembre
        self.assertEqual(occurrences[0][0], later)

    def test_end_date_stops_the_calendar(self):
        sub = self._make_subscription(
            "monthly", 100.0, end_date=self.date_start + relativedelta(months=2)
        )
        self.assertEqual(len(sub._budget_occurrences(self.date_start, self.date_end)), 3)

    def test_a_cancelled_subscription_still_counts_what_it_cost(self):
        """🔴 Un abonnement résilié a coûté quelque chose avant de l'être.

        L'ignorer ferait un théorique trop bas, donc une dérive trop haute, donc
        de fausses alertes sur un poste qui va bien. Sans date de fin, la
        dernière facture reçue fait foi.
        """
        sub = self._make_subscription("monthly", 100.0)
        for month in range(3):
            self._vendor_bill(
                sub, self.account_software, 100.0,
                self.date_start + relativedelta(months=month),
            )
        sub.state = "cancelled"
        sub.invalidate_recordset()
        self.assertEqual(sub.last_vendor_bill_date, self.date_start + relativedelta(months=2))
        occurrences = sub._budget_occurrences(self.date_start, self.date_end)
        self.assertEqual(len(occurrences), 3)

    def test_a_cancelled_subscription_without_a_bill_stops_at_its_start(self):
        sub = self._make_subscription("monthly", 100.0, state="cancelled")
        self.assertEqual(len(sub._budget_occurrences(self.date_start, self.date_end)), 1)

    def test_the_window_is_respected_in_both_directions(self):
        sub = self._make_subscription("monthly", 100.0)
        milieu = self.date_start + relativedelta(months=5)
        occurrences = sub._budget_occurrences(milieu, self.date_end)
        self.assertTrue(all(date >= milieu for date, _amount in occurrences))
        self.assertEqual(len(occurrences), 7)  # juin à décembre
