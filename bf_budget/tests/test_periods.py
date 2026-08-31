from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import BfBudgetCommon


@tagged("post_install", "-at_install")
class TestBfBudgetPeriods(BfBudgetCommon):
    def test_periods_are_created_with_the_line(self):
        """⚠️ Posées DANS `create`, jamais par un `default` : un calculé stocké
        doublé d'un défaut ne joue jamais son calcul à la création."""
        budget = self._make_budget()
        self.assertEqual(len(budget.line_ids.period_ids), 12)

    def test_typing_a_total_spreads_it(self):
        budget = self._make_budget(planned=1200.0)
        line = budget.line_ids
        self.assertEqual(line.amount_planned, 1200.0)
        self.assertEqual(sum(line.period_ids.mapped("amount_planned")), 1200.0)
        self.assertEqual(line.period_ids.sorted("date_start")[0].amount_planned, 100.0)

    def test_the_remainder_lands_on_the_last_month(self):
        """Un total qui ne se divise pas laisse des cents : le dernier mois les
        reprend, visiblement, plutôt qu'une redistribution en douce."""
        budget = self._make_budget(planned=1000.0)
        periods = budget.line_ids.period_ids.sorted("date_start")
        self.assertEqual(periods[0].amount_planned, 83.33)
        self.assertAlmostEqual(periods[-1].amount_planned, 1000.0 - 83.33 * 11, places=2)
        self.assertAlmostEqual(sum(periods.mapped("amount_planned")), 1000.0, places=2)

    def test_editing_a_month_updates_the_total(self):
        budget = self._make_budget(planned=1200.0)
        line = budget.line_ids
        line.period_ids.sorted("date_start")[0].amount_planned = 500.0
        line.invalidate_recordset()
        self.assertEqual(line.amount_planned, 1600.0)

    def test_periods_cover_the_exercise_without_overflow(self):
        budget = self._make_budget()
        periods = budget.line_ids.period_ids.sorted("date_start")
        self.assertEqual(periods[0].date_start, self.date_start)
        self.assertEqual(periods[-1].date_end, self.date_end)

    def test_regenerating_is_refused_once_open(self):
        budget = self._make_budget(state="open")
        with self.assertRaises(UserError):
            budget.line_ids.action_regenerate_periods()
