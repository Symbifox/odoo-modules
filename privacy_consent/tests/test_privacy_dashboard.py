from datetime import timedelta

from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged("privacy_consent", "privacy_dashboard")
class TestPrivacyDashboard(TransactionCase):
    """Test cases for Privacy Dashboard.

    Stats are computed DB-wide, so tests measure deltas against
    a baseline rather than absolute values.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env["res.partner"].create({
            "name": "Test Partner",
            "email": "test@example.com",
        })
        cls.purpose = cls.env["privacy.purpose"].create({
            "code": "TEST_DASH",
            "name": "Test Dashboard Purpose",
            "default_validity_days": 365,
        })

    def test_dashboard_stats_empty(self):
        """Dashboard returns expected keys with integer counts."""
        Dashboard = self.env["privacy.dashboard"]
        stats = Dashboard._get_dashboard_stats()

        for key in ("pending_requests", "granted_consents", "consent_rate"):
            self.assertIn(key, stats)
            self.assertIsInstance(stats[key], (int, float))

    def test_dashboard_stats_with_consents(self):
        """New consents increment pending/granted/expiring counters."""
        Consent = self.env["privacy.consent"]
        Dashboard = self.env["privacy.dashboard"]

        before = Dashboard._get_dashboard_stats()

        Consent.create({
            "subject_partner_id": self.partner.id,
            "purpose_id": self.purpose.id,
            "status": "pending",
        })
        Consent.create({
            "subject_partner_id": self.partner.id,
            "purpose_id": self.purpose.id,
            "status": "granted",
            "granted_at": fields.Datetime.now(),
            "expires_at": fields.Datetime.now() + timedelta(days=20),
        })

        after = Dashboard._get_dashboard_stats()

        self.assertEqual(after["pending_requests"] - before["pending_requests"], 1)
        self.assertEqual(after["granted_consents"] - before["granted_consents"], 1)
        self.assertEqual(after["expiring_30_days"] - before["expiring_30_days"], 1)

    def test_dashboard_consent_rate(self):
        """Consent rate reflects granted/(granted+refused) ratio."""
        Consent = self.env["privacy.consent"]
        Dashboard = self.env["privacy.dashboard"]

        partner = self.env["res.partner"].create({
            "name": "Rate Partner",
            "email": "rate@example.com",
        })

        before = Dashboard._get_dashboard_stats()
        granted_before = before["granted_consents"]
        refused_before = Consent.search_count([("status", "=", "refused")])

        for _ in range(3):
            Consent.create({
                "subject_partner_id": partner.id,
                "purpose_id": self.purpose.id,
                "status": "granted",
                "granted_at": fields.Datetime.now(),
            })
        Consent.create({
            "subject_partner_id": partner.id,
            "purpose_id": self.purpose.id,
            "status": "refused",
            "refused_at": fields.Datetime.now(),
        })

        after = Dashboard._get_dashboard_stats()

        granted_after = after["granted_consents"]
        refused_after = Consent.search_count([("status", "=", "refused")])
        expected_rate = round(granted_after / (granted_after + refused_after) * 100)

        self.assertEqual(granted_after - granted_before, 3)
        self.assertEqual(refused_after - refused_before, 1)
        self.assertEqual(after["consent_rate"], expected_rate)

    def test_dashboard_action_view_pending(self):
        """Test action to view pending requests."""
        Dashboard = self.env["privacy.dashboard"]
        dashboard = Dashboard.create({})

        action = dashboard.action_view_pending_requests()

        self.assertEqual(action["type"], "ir.actions.act_window")
        self.assertEqual(action["res_model"], "privacy.consent")
        self.assertIn(("status", "=", "pending"), action["domain"])
