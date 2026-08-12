from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged


@tagged("privacy_consent", "privacy_retention_calendar")
class TestPrivacyRetentionCalendar(TransactionCase):
    """Test cases for retention calendar rules (v3.0.0)."""

    def test_default_rules_seeded(self):
        """The 8 default retention rules must be loaded."""
        Calendar = self.env["privacy.retention.calendar"]
        expected_codes = {
            "CTR-001", "FIN-001", "RH-001", "PRJ-001",
            "COR-001", "MED-001", "SEC-001", "CST-001",
        }
        found = Calendar.search([("code", "in", list(expected_codes))])
        self.assertEqual(set(found.mapped("code")), expected_codes)

    def test_total_retention_days_compute(self):
        """total_retention_days = (active + semi_active) * 365."""
        rule = self.env["privacy.retention.calendar"].create({
            "name": "Test rule",
            "code": "TEST-COMPUTE",
            "document_type": "contract",
            "legal_basis": "Test",
            "active_retention_years": 3,
            "semi_active_retention_years": 4,
        })
        self.assertEqual(rule.total_retention_days, 7 * 365)

        rule.active_retention_years = 1
        self.assertEqual(rule.total_retention_days, 5 * 365)

    def test_code_unique_per_company(self):
        """Code must be unique per company."""
        Calendar = self.env["privacy.retention.calendar"]
        Calendar.create({
            "name": "First",
            "code": "UNIQ-TEST",
            "document_type": "contract",
            "legal_basis": "Test",
        })
        with self.assertRaises(Exception):
            Calendar.create({
                "name": "Second",
                "code": "UNIQ-TEST",
                "document_type": "invoice",
                "legal_basis": "Test",
            })

    def test_action_view_classifications_returns_action(self):
        """action_view_classifications returns an act_window (not None)."""
        rule = self.env["privacy.retention.calendar"].search([("code", "=", "CTR-001")], limit=1)
        self.assertTrue(rule)
        action = rule.action_view_classifications()
        self.assertEqual(action["type"], "ir.actions.act_window")
        self.assertEqual(action["res_model"], "privacy.document.classification")

    def test_action_create_campaign_returns_action(self):
        """action_create_campaign returns an act_window."""
        rule = self.env["privacy.retention.calendar"].search([("code", "=", "FIN-001")], limit=1)
        action = rule.action_create_campaign()
        self.assertEqual(action["type"], "ir.actions.act_window")
        self.assertEqual(action["res_model"], "privacy.destruction.campaign")
        self.assertEqual(action["context"]["default_retention_calendar_id"], rule.id)
