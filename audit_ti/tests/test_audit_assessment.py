from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged
from psycopg2 import IntegrityError
from odoo.tools import mute_logger


@tagged("audit_ti", "audit_assessment")
class TestAuditAssessment(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.client = cls.env["audit.client"].create({"name": "Test Client"})
        cls.supplier = cls.env["audit.supplier"].create({
            "name": "Test Supplier",
            "supplier_type": "saas",
        })
        # Reuse an existing element loaded from data files
        cls.element = cls.env["audit.element"].search([], limit=1)
        cls.assertTrue = cls.assertTrue

    def _make_assessment(self, **overrides):
        vals = {
            "client_id": self.client.id,
            "supplier_id": self.supplier.id,
            "element_id": self.element.id,
            "status": "pending",
        }
        vals.update(overrides)
        return self.env["audit.assessment"].create(vals)

    def test_create_assessment_defaults(self):
        """A freshly created assessment is pending with no response."""
        a = self._make_assessment()
        self.assertEqual(a.status, "pending")
        self.assertFalse(a.response)
        self.assertFalse(a.response_received_date)

    def test_sql_constraint_unique_triplet(self):
        """(client, supplier, element) triplet must be unique."""
        self._make_assessment()
        with self.assertRaises(IntegrityError), mute_logger("odoo.sql_db"):
            with self.env.cr.savepoint():
                self._make_assessment()

    def test_write_autofills_response_received_date(self):
        """Writing a response on an empty assessment auto-sets the reception date."""
        a = self._make_assessment()
        self.assertFalse(a.response_received_date)
        a.write({"response": "Le fournisseur a répondu par courriel."})
        self.assertEqual(a.response_received_date, fields.Date.today())

    def test_write_preserves_existing_response_date(self):
        """Updating the response does not overwrite an already-set reception date."""
        fixed_date = fields.Date.from_string("2025-01-15")
        a = self._make_assessment(
            response="Réponse initiale",
            response_received_date=fixed_date,
        )
        a.write({"response": "Réponse mise à jour"})
        self.assertEqual(a.response_received_date, fixed_date)

    def test_action_create_watchpoint_returns_window_action(self):
        """action_create_watchpoint returns a form action with proper defaults."""
        a = self._make_assessment(status="inadequate")
        action = a.action_create_watchpoint()
        self.assertEqual(action["type"], "ir.actions.act_window")
        self.assertEqual(action["res_model"], "audit.watchpoint")
        self.assertEqual(action["target"], "new")
        ctx = action["context"]
        self.assertEqual(ctx["default_client_id"], self.client.id)
        self.assertEqual(ctx["default_element_id"], self.element.id)
        self.assertEqual(ctx["default_source"], "supplier_response")
        self.assertIn(self.supplier.name, ctx["default_description"])

    def test_element_num_related_stored(self):
        """element_num is a stored related field and reflects the element's num."""
        a = self._make_assessment()
        self.assertEqual(a.element_num, self.element.num)

    def test_status_transitions(self):
        """Status can move through the selection values."""
        a = self._make_assessment()
        for status in ("to_validate", "adequate", "partial", "inadequate", "na"):
            a.status = status
            self.assertEqual(a.status, status)
