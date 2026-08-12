from datetime import timedelta

from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged("privacy_consent", "privacy_retention")
class TestPrivacyRetention(TransactionCase):
    """Test cases for Privacy Retention and Destruction."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env["res.partner"].create({
            "name": "Test Partner",
            "email": "test@example.com",
        })
        cls.purpose = cls.env["privacy.purpose"].create({
            "code": "RETENTION_TEST",
            "name": "Retention Test Purpose",
            "default_validity_days": 30,
        })

    def test_create_retention_policy(self):
        """Test creating a retention policy."""
        Policy = self.env["privacy.retention.policy"]

        policy = Policy.create({
            "purpose_id": self.purpose.id,
            "retention_days": 90,
            "trigger_on": "both",
            "destruction_method": "anonymize",
        })

        self.assertEqual(policy.retention_days, 90)
        self.assertEqual(policy.destruction_method, "anonymize")
        self.assertTrue(policy.active)

    def test_destruction_request_creation(self):
        """Test creating a destruction request."""
        Policy = self.env["privacy.retention.policy"]
        Consent = self.env["privacy.consent"]
        Destruction = self.env["privacy.destruction.request"]

        # Create policy
        policy = Policy.create({
            "purpose_id": self.purpose.id,
            "retention_days": 30,
            "trigger_on": "expiration",
            "destruction_method": "anonymize",
        })

        # Create expired consent
        consent = Consent.create({
            "subject_partner_id": self.partner.id,
            "purpose_id": self.purpose.id,
            "status": "expired",
            "expires_at": fields.Datetime.now() - timedelta(days=1),
        })

        # Create destruction request
        destruction = Destruction.create({
            "consent_id": consent.id,
            "policy_id": policy.id,
            "trigger_date": consent.expires_at,
            "scheduled_date": fields.Date.today() + timedelta(days=30),
        })

        self.assertTrue(destruction.certificate_number)
        self.assertEqual(destruction.state, "pending")
        self.assertEqual(destruction.partner_id, self.partner)

    def test_destruction_workflow(self):
        """Test destruction request workflow."""
        Policy = self.env["privacy.retention.policy"]
        Consent = self.env["privacy.consent"]
        Destruction = self.env["privacy.destruction.request"]

        policy = Policy.create({
            "purpose_id": self.purpose.id,
            "retention_days": 0,
            "destruction_method": "archive",
        })

        consent = Consent.create({
            "subject_partner_id": self.partner.id,
            "purpose_id": self.purpose.id,
            "status": "expired",
        })

        destruction = Destruction.create({
            "consent_id": consent.id,
            "policy_id": policy.id,
            "trigger_date": fields.Datetime.now(),
            "scheduled_date": fields.Date.today(),
        })

        # Test approval
        destruction.action_approve()
        self.assertEqual(destruction.state, "approved")

        # Test execution
        destruction.action_execute()
        self.assertEqual(destruction.state, "executed")
        self.assertTrue(destruction.executed_at)
        self.assertTrue(destruction.verification_hash)

    def test_destruction_cancellation(self):
        """Test cancelling a destruction request."""
        Policy = self.env["privacy.retention.policy"]
        Consent = self.env["privacy.consent"]
        Destruction = self.env["privacy.destruction.request"]

        policy = Policy.create({
            "purpose_id": self.purpose.id,
            "retention_days": 30,
        })

        consent = Consent.create({
            "subject_partner_id": self.partner.id,
            "purpose_id": self.purpose.id,
            "status": "expired",
        })

        destruction = Destruction.create({
            "consent_id": consent.id,
            "policy_id": policy.id,
            "trigger_date": fields.Datetime.now(),
            "scheduled_date": fields.Date.today(),
        })

        destruction.action_cancel()
        self.assertEqual(destruction.state, "cancelled")
