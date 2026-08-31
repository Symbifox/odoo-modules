from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import BfBudgetCommon


@tagged("post_install", "-at_install")
class TestBfBudgetState(BfBudgetCommon):
    def test_cannot_open_an_empty_budget(self):
        budget = self.env["bf.budget"].create(
            {
                "name": "Vide",
                "company_id": self.company.id,
                "date_start": self.date_start,
                "date_end": self.date_end,
            }
        )
        with self.assertRaises(UserError):
            budget.action_open()

    def test_open_budget_freezes_the_plan(self):
        """🔴 La contre-épreuve : c'est bien le gel qui refuse, pas autre chose."""
        budget = self._make_budget(state="open")
        with self.assertRaises(UserError):
            budget.write({"date_end": self.date_end.replace(month=11, day=30)})
        # Le suivi, lui, reste ouvert : geler le plan n'est pas geler la conversation.
        budget.write({"alert_threshold_pct": 25.0})
        self.assertEqual(budget.alert_threshold_pct, 25.0)
        # Et remis au brouillon, le plan redevient modifiable.
        budget.action_reset_draft()
        budget.write({"date_end": self.date_end.replace(month=11, day=30)})
        self.assertEqual(budget.date_end.month, 11)

    def test_revision_is_a_new_record(self):
        budget = self._make_budget(state="open")
        action = budget.action_create_revision()
        revision = self.env["bf.budget"].browse(action["res_id"])
        self.assertNotEqual(revision.id, budget.id)
        self.assertEqual(budget.state, "revised")
        self.assertEqual(revision.state, "draft")
        self.assertEqual(revision.revision, 1)
        self.assertEqual(revision.revision_of_id, budget)
        self.assertEqual(len(revision.line_ids), len(budget.line_ids))

    def test_revision_refused_on_a_draft(self):
        budget = self._make_budget()
        with self.assertRaises(UserError):
            budget.action_create_revision()

    def test_opened_budget_is_not_deleted(self):
        budget = self._make_budget(state="open")
        with self.assertRaises(UserError):
            budget.unlink()

    def test_revised_budget_is_not_cancelled(self):
        budget = self._make_budget(state="open")
        budget.action_create_revision()
        with self.assertRaises(UserError):
            budget.action_cancel()

    def test_draft_budget_is_deleted(self):
        budget = self._make_budget()
        budget_id = budget.id
        budget.unlink()
        self.assertFalse(self.env["bf.budget"].browse(budget_id).exists())
