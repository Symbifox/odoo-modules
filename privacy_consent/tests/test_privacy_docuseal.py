from unittest.mock import patch, MagicMock

from odoo import fields
from odoo.tests import TransactionCase, tagged
from odoo.exceptions import UserError


@tagged("privacy_consent", "privacy_docuseal")
class TestPrivacyDocuSeal(TransactionCase):
    """Test cases for DocuSeal integration."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env["res.partner"].create({
            "name": "Test Partner",
            "email": "test@example.com",
        })
        cls.purpose = cls.env["privacy.purpose"].create({
            "code": "DOCUSEAL_TEST",
            "name": "DocuSeal Test Purpose",
            "default_validity_days": 365,
        })

    def test_docuseal_config_creation(self):
        """Test creating DocuSeal configuration."""
        Config = self.env["privacy.docuseal.config"]

        config = Config.create({
            "name": "Test Config",
            "api_url": "https://api.docuseal.co",
            "api_key": "test_api_key_123",
        })

        self.assertTrue(config.api_key_encrypted)
        # Decrypted value should match
        self.assertEqual(config.api_key, "test_api_key_123")

    def test_docuseal_template_mapping(self):
        """Test DocuSeal template mapping."""
        Template = self.env["privacy.docuseal.template"]

        template = Template.create({
            "name": "Test Template",
            "purpose_id": self.purpose.id,
            "docuseal_template_id": "12345",
            "field_mapping": '{"name": "subject_partner_id.name"}',
        })

        self.assertEqual(template.docuseal_template_id, "12345")
        self.assertTrue(template.active)

    def test_docuseal_field_values(self):
        """Test field value extraction from consent."""
        Template = self.env["privacy.docuseal.template"]
        Consent = self.env["privacy.consent"]

        template = Template.create({
            "name": "Test Template",
            "purpose_id": self.purpose.id,
            "docuseal_template_id": "12345",
            "field_mapping": '{"partner_name": "subject_partner_id.name", "partner_email": "subject_partner_id.email"}',
        })

        consent = Consent.create({
            "subject_partner_id": self.partner.id,
            "purpose_id": self.purpose.id,
        })

        values = template.get_field_values(consent)

        self.assertEqual(values["partner_name"], "Test Partner")
        self.assertEqual(values["partner_email"], "test@example.com")

    def test_consent_docuseal_fields(self):
        """Test DocuSeal fields on consent model."""
        Consent = self.env["privacy.consent"]

        consent = Consent.create({
            "subject_partner_id": self.partner.id,
            "purpose_id": self.purpose.id,
        })

        # Initially empty
        self.assertFalse(consent.docuseal_submission_id)
        self.assertFalse(consent.docuseal_status)

        # Update DocuSeal fields
        consent.write({
            "docuseal_submission_id": "sub_123",
            "docuseal_status": "pending",
            "docuseal_sent_at": fields.Datetime.now(),
        })

        self.assertEqual(consent.docuseal_submission_id, "sub_123")
        self.assertEqual(consent.docuseal_status, "pending")

    def test_docuseal_completion_processing(self):
        """Test processing DocuSeal completion."""
        Consent = self.env["privacy.consent"]

        consent = Consent.create({
            "subject_partner_id": self.partner.id,
            "purpose_id": self.purpose.id,
            "status": "pending",
            "docuseal_submission_id": "sub_123",
            "docuseal_status": "pending",
        })

        # Process completion
        consent._process_docuseal_completion({}, documents=None)

        self.assertEqual(consent.docuseal_status, "completed")
        self.assertEqual(consent.status, "granted")
        self.assertEqual(consent.collection_method, "signature")
