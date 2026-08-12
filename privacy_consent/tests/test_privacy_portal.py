from datetime import timedelta

from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged("privacy_consent", "privacy_portal")
class TestPrivacyPortal(TransactionCase):
    """Test cases for Portal functionality."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env["res.partner"].create({
            "name": "Test Partner",
            "email": "test@example.com",
        })
        cls.purpose = cls.env["privacy.purpose"].create({
            "code": "PORTAL_TEST",
            "name": "Portal Test Purpose",
            "default_validity_days": 365,
        })

    def test_consent_renewal(self):
        """Test consent renewal creates new consent."""
        Consent = self.env["privacy.consent"]

        original = Consent.create({
            "subject_partner_id": self.partner.id,
            "purpose_id": self.purpose.id,
            "status": "granted",
            "granted_at": fields.Datetime.now() - timedelta(days=300),
            "expires_at": fields.Datetime.now() + timedelta(days=65),
        })

        # Perform renewal
        action = original.action_renew()

        # Check new consent was created
        new_consent = Consent.browse(action["res_id"])
        self.assertTrue(new_consent.exists())
        self.assertEqual(new_consent.status, "draft")
        self.assertEqual(new_consent.renewed_from_id, original)
        self.assertEqual(original.renewed_to_id, new_consent)

    def test_consent_renewal_chain(self):
        """Test viewing renewal history."""
        Consent = self.env["privacy.consent"]

        # Create chain of renewals
        first = Consent.create({
            "subject_partner_id": self.partner.id,
            "purpose_id": self.purpose.id,
            "status": "expired",
        })

        second = first.copy({
            "status": "expired",
            "renewed_from_id": first.id,
        })
        first.renewed_to_id = second.id

        third = second.copy({
            "status": "granted",
            "renewed_from_id": second.id,
        })
        second.renewed_to_id = third.id

        # View history from middle consent
        action = second.action_view_renewal_history()

        self.assertEqual(action["res_model"], "privacy.consent")
        self.assertIn(first.id, action["domain"][0][2])
        self.assertIn(second.id, action["domain"][0][2])
        self.assertIn(third.id, action["domain"][0][2])

    def test_cannot_renew_pending(self):
        """Test that pending consents cannot be renewed."""
        Consent = self.env["privacy.consent"]

        consent = Consent.create({
            "subject_partner_id": self.partner.id,
            "purpose_id": self.purpose.id,
            "status": "pending",
        })

        with self.assertRaises(Exception):
            consent.action_renew()

    def test_cannot_renew_twice(self):
        """Test that a consent can only be renewed once."""
        Consent = self.env["privacy.consent"]

        original = Consent.create({
            "subject_partner_id": self.partner.id,
            "purpose_id": self.purpose.id,
            "status": "granted",
            "granted_at": fields.Datetime.now(),
        })

        # First renewal
        original.action_renew()

        # Second renewal should fail
        with self.assertRaises(Exception):
            original.action_renew()
