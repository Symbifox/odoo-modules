from datetime import timedelta

from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged("privacy_consent", "privacy_email")
class TestPrivacyEmail(TransactionCase):
    """Test cases for Email sequences and automation."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env["res.partner"].create({
            "name": "Test Partner",
            "email": "test@example.com",
        })
        cls.purpose = cls.env["privacy.purpose"].create({
            "code": "EMAIL_TEST",
            "name": "Email Test Purpose",
            "default_validity_days": 365,
            "auto_expire_pending_days": 30,
        })

    def test_email_sequence_creation(self):
        """Test creating email sequences."""
        Sequence = self.env["privacy.email.sequence"]

        # Get or create a mail template
        template = self.env["mail.template"].search([
            ("model_id.model", "=", "privacy.consent")
        ], limit=1)

        if not template:
            template = self.env["mail.template"].create({
                "name": "Test Template",
                "model_id": self.env["ir.model"]._get("privacy.consent").id,
            })

        seq1 = Sequence.create({
            "name": "First Reminder",
            "purpose_id": self.purpose.id,
            "sequence": 1,
            "days_after_previous": 7,
            "mail_template_id": template.id,
        })

        seq2 = Sequence.create({
            "name": "Second Reminder",
            "purpose_id": self.purpose.id,
            "sequence": 2,
            "days_after_previous": 14,
            "mail_template_id": template.id,
        })

        self.assertEqual(seq1.sequence, 1)
        self.assertEqual(seq2.sequence, 2)
        self.assertEqual(seq1.days_after_previous, 7)
        self.assertEqual(seq2.days_after_previous, 14)

    def test_auto_expire_pending_days(self):
        """Test auto-expire pending days configuration."""
        self.assertEqual(self.purpose.auto_expire_pending_days, 30)

    def test_consent_reminder_tracking(self):
        """Test consent reminder count tracking."""
        Consent = self.env["privacy.consent"]

        consent = Consent.create({
            "subject_partner_id": self.partner.id,
            "purpose_id": self.purpose.id,
            "status": "pending",
            "requested_at": fields.Datetime.now() - timedelta(days=10),
        })

        self.assertEqual(consent.reminder_count, 0)
        self.assertFalse(consent.last_reminder_sent_at)

        # Simulate reminder sent
        consent.write({
            "reminder_count": 1,
            "last_reminder_sent_at": fields.Datetime.now(),
        })

        self.assertEqual(consent.reminder_count, 1)
        self.assertTrue(consent.last_reminder_sent_at)

    def test_cron_auto_expire_pending(self):
        """Test auto-expire cron job."""
        Consent = self.env["privacy.consent"]

        # Create old pending consent
        old_consent = Consent.create({
            "subject_partner_id": self.partner.id,
            "purpose_id": self.purpose.id,
            "status": "pending",
            "requested_at": fields.Datetime.now() - timedelta(days=35),
        })

        # Create recent pending consent
        new_consent = Consent.create({
            "subject_partner_id": self.partner.id,
            "purpose_id": self.purpose.id,
            "status": "pending",
            "requested_at": fields.Datetime.now() - timedelta(days=5),
        })

        # Run cron
        Consent.cron_auto_expire_pending()

        # Old consent should be expired
        old_consent.invalidate_recordset()
        self.assertEqual(old_consent.status, "expired")

        # New consent should still be pending
        new_consent.invalidate_recordset()
        self.assertEqual(new_consent.status, "pending")

    def test_purpose_email_sequence_relation(self):
        """Test purpose has email sequences relation."""
        self.purpose.invalidate_recordset()
        # Just verify the field exists and is accessible
        self.assertIsNotNone(self.purpose.email_sequence_ids)
