from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged("audit_ti", "audit_client")
class TestAuditClient(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Client = cls.env["audit.client"]
        cls.Supplier = cls.env["audit.supplier"]
        cls.Assessment = cls.env["audit.assessment"]
        cls.Element = cls.env["audit.element"]

        cls.client = cls.Client.create({"name": "ACME Inc."})
        cls.supplier = cls.Supplier.create({
            "name": "Acme Cloud",
            "supplier_type": "cloud",
        })
        cls.element = cls.Element.search([], limit=1)

    def _make_assessment(self, status, supplier=None, element=None):
        return self.Assessment.create({
            "client_id": self.client.id,
            "supplier_id": (supplier or self.supplier).id,
            "element_id": (element or self.element).id,
            "status": status,
        })

    # --- Lifecycle ---

    def test_create_client_defaults(self):
        c = self.Client.create({"name": "Fresh Client"})
        self.assertEqual(c.state, "in_progress")
        self.assertTrue(c.active)
        self.assertFalse(c.delivered_date)
        self.assertFalse(c.delivered_by)

    def test_action_deliver_sets_metadata(self):
        self.client.action_deliver()
        self.assertEqual(self.client.state, "delivered")
        self.assertEqual(self.client.delivered_date, fields.Date.today())
        self.assertEqual(self.client.delivered_by, self.env.user)

    def test_action_reopen_clears_metadata(self):
        self.client.action_deliver()
        self.client.action_reopen()
        self.assertEqual(self.client.state, "in_progress")
        self.assertFalse(self.client.delivered_date)
        self.assertFalse(self.client.delivered_by)

    # --- Computed statistics ---

    def test_stats_no_assessments(self):
        self.assertEqual(self.client.assessment_count, 0)
        self.assertEqual(self.client.progress_pct, 0)
        self.assertEqual(self.client.adequate_count, 0)
        self.assertEqual(self.client.rag_green, 0)
        self.assertEqual(self.client.rag_red, 0)

    def test_progress_pct_ignores_pending_and_to_validate(self):
        """Progress counts only definitive statuses (not pending / to_validate)."""
        el2 = self.Element.search([("id", "!=", self.element.id)], limit=1)
        self._make_assessment("adequate")
        self._make_assessment("pending", element=el2)
        self.client.invalidate_recordset()
        self.assertEqual(self.client.assessment_count, 2)
        self.assertEqual(self.client.progress_pct, 50.0)

    def test_adequate_count(self):
        el2 = self.Element.search([("id", "!=", self.element.id)], limit=1)
        self._make_assessment("adequate")
        self._make_assessment("partial", element=el2)
        self.client.invalidate_recordset()
        self.assertEqual(self.client.adequate_count, 1)

    # --- RAG logic (static method) ---

    def test_element_rag_green_all_adequate(self):
        self.assertEqual(self.Client._element_rag(["adequate", "adequate"]), "green")

    def test_element_rag_red_any_inadequate(self):
        self.assertEqual(
            self.Client._element_rag(["adequate", "inadequate"]), "red"
        )

    def test_element_rag_yellow_mix_no_inadequate(self):
        self.assertEqual(
            self.Client._element_rag(["adequate", "partial"]), "yellow"
        )
        self.assertEqual(
            self.Client._element_rag(["adequate", "declared"]), "yellow"
        )

    def test_element_rag_grey_no_relevant(self):
        self.assertEqual(self.Client._element_rag([]), "grey")
        self.assertEqual(self.Client._element_rag(["na", "pending"]), "grey")
        self.assertEqual(self.Client._element_rag(["to_validate"]), "grey")

    # --- _best_status ---

    def test_best_status_follows_hierarchy(self):
        best = self.Client._best_status(["partial", "adequate", "inadequate"])
        self.assertEqual(best, "adequate")

    def test_best_status_empty_defaults_pending(self):
        self.assertEqual(self.Client._best_status([]), "pending")

    # --- action_print_progress_report ---

    def test_action_print_progress_report_returns_report_action(self):
        # Skip the external-report-layout wizard that core triggers for admins
        # whose company has no layout configured yet.
        action = self.client.with_context(
            discard_logo_check=True
        ).action_print_progress_report()
        self.assertEqual(action["type"], "ir.actions.report")
