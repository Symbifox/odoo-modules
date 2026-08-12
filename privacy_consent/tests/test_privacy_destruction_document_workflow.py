from datetime import timedelta

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged("privacy_consent", "privacy_destruction_document")
class TestPrivacyDestructionDocumentWorkflow(TransactionCase):
    """Test cases for destruction requests of type='document' (v3.0.0)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Destruction = cls.env["privacy.destruction.request"]
        cls.Classification = cls.env["privacy.document.classification"]
        cls.Register = cls.env["privacy.destruction.register"]

        cls.officer_group = cls.env.ref("privacy_consent.group_privacy_officer")
        cls.manager_group = cls.env.ref("privacy_consent.group_privacy_manager")
        cls.officer = cls.env["res.users"].create({
            "name": "Doc Officer",
            "login": "test_doc_officer",
            "email": "doc-officer@example.com",
            "groups_id": [(4, cls.officer_group.id)],
        })
        cls.manager_user = cls.env["res.users"].create({
            "name": "Doc Manager",
            "login": "test_doc_manager",
            "email": "doc-manager@example.com",
            "groups_id": [(4, cls.manager_group.id)],
        })

        cls.partner = cls.env["res.partner"].create({"name": "Doc Subject"})
        cls.calendar = cls.env["privacy.retention.calendar"].create({
            "name": "Doc retention",
            "code": "DOC-WF",
            "document_type": "contract",
            "legal_basis": "Test",
            "active_retention_years": 0,
            "destruction_method": "delete",
        })
        cls.classification = cls.Classification.create({
            "res_model": "res.partner",
            "res_id": cls.partner.id,
            "pi_category": "identification",
            "retention_calendar_id": cls.calendar.id,
        })

    def _create_document_request(self):
        return self.Destruction.create({
            "request_type": "document",
            "classification_ids": [(6, 0, [self.classification.id])],
            "retention_calendar_id": self.calendar.id,
            "trigger_date": fields.Datetime.now(),
            "scheduled_date": fields.Date.today(),
        })

    def test_create_document_request(self):
        """A document-type request gets a certificate number."""
        req = self._create_document_request()
        self.assertEqual(req.request_type, "document")
        self.assertTrue(req.certificate_number)
        self.assertEqual(req.state, "pending")

    def test_workflow_pending_approved_executed(self):
        """Workflow creates a register entry on execute."""
        req = self._create_document_request()
        req.with_user(self.manager_user).action_approve()
        self.assertEqual(req.state, "approved")

        initial = self.Register.search_count([])
        req.with_user(self.officer).action_execute()
        self.assertEqual(req.state, "executed")
        self.assertTrue(req.executed_at)
        self.assertTrue(req.verification_hash)
        self.assertEqual(self.Register.search_count([]) - initial, 1)

    def test_execute_requires_officer(self):
        """Manager cannot execute (officer-only gate)."""
        req = self._create_document_request()
        req.with_user(self.manager_user).action_approve()
        with self.assertRaises(UserError):
            req.with_user(self.manager_user).action_execute()

    def test_approve_requires_manager(self):
        """Plain privacy user cannot approve."""
        plain = self.env["res.users"].create({
            "name": "Plain doc user",
            "login": "test_doc_plain",
            "email": "doc-plain@example.com",
            "groups_id": [(4, self.env.ref("privacy_consent.group_privacy_user").id)],
        })
        req = self._create_document_request()
        with self.assertRaises(UserError):
            req.with_user(plain).action_approve()

    def test_destruction_method_used_has_secure_wipe(self):
        """destruction_method_used selection includes secure_wipe (fix #2)."""
        selection_vals = dict(
            self.Destruction._fields["destruction_method_used"]._description_selection(self.env)
        )
        self.assertIn("secure_wipe", selection_vals)

    def test_action_view_certificate_returns_report(self):
        """action_view_certificate returns a report action (fix #4)."""
        req = self._create_document_request()
        req.with_user(self.manager_user).action_approve()
        req.with_user(self.officer).action_execute()
        action = req.with_user(self.officer).action_view_certificate()
        self.assertIsInstance(action, dict)
        self.assertEqual(action.get("type"), "ir.actions.report")

    def test_state_machine_blocks_direct_skip(self):
        """Direct write to skip pending→executed raises UserError (v3.1.0 C2 fix)."""
        req = self._create_document_request()
        with self.assertRaises(UserError):
            req.write({"state": "executed"})

    def test_state_machine_blocks_reverse_transition(self):
        """Cannot revert executed → pending."""
        req = self._create_document_request()
        req.with_user(self.manager_user).action_approve()
        req.with_user(self.officer).action_execute()
        with self.assertRaises(UserError):
            req.write({"state": "pending"})

    def test_concurrent_approval_on_same_classification_blocked(self):
        """A second request targeting the same classification cannot be approved."""
        first = self._create_document_request()
        second = self._create_document_request()
        first.with_user(self.manager_user).action_approve()
        with self.assertRaises(UserError):
            second.with_user(self.manager_user).action_approve()

    def test_register_entry_links_to_request(self):
        """The register entry created on execute references the destruction request."""
        req = self._create_document_request()
        req.with_user(self.manager_user).action_approve()
        req.with_user(self.officer).action_execute()
        self.assertTrue(req.register_entry_ids)
        entry = req.register_entry_ids[0]
        self.assertEqual(entry.destruction_request_id, req)
        self.assertTrue(entry.verification_hash)
