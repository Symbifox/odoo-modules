"""Loi 25 testimonial locks: no publication without a granted consent,
automatic retirement on withdrawal."""
from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestPrivacyLocks(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env["res.partner"].create(
            {"name": "Client Témoin", "email": "temoin@example.com"}
        )
        cls.notice = cls.env.ref("bf_cx_privacy.notice_testimonial")
        cls.testimonial = cls.env["bf.cx.testimonial"].create(
            {
                "name": "Témoignage",
                "partner_id": cls.partner.id,
                "body": "Un service hors pair.",
                "consent_mode": "privacy",
            }
        )

    def _granted_consent(self):
        consent = self.env["privacy.consent"].create(
            {
                "subject_partner_id": self.partner.id,
                "notice_id": self.notice.id,
                "status": "pending",
                "requested_at": fields.Datetime.now(),
                "collection_method": "email",
            }
        )
        consent.action_grant()
        return consent

    def test_privacy_mode_requires_granted_consent(self):
        with self.assertRaises(UserError):
            self.testimonial.action_set_consented()

    def test_grant_then_publish_then_withdraw(self):
        consent = self._granted_consent()
        self.testimonial.privacy_consent_id = consent
        self.testimonial.action_check_privacy_consent()
        self.assertEqual(self.testimonial.state, "consented")
        self.testimonial.action_publish()
        self.assertEqual(self.testimonial.state, "published")
        # Withdrawal must retire the published testimonial automatically.
        consent.write({"status": "withdrawn"})
        self.assertEqual(self.testimonial.state, "retired")

    def test_do_not_contact_blocks_solicitation(self):
        self.env["privacy.contact.preference"].create(
            {"partner_id": self.partner.id, "do_not_contact": True}
        )
        self.partner.invalidate_recordset()
        allowed, blocked = self.partner._bf_cx_split_solicitable()
        self.assertFalse(allowed)
        self.assertEqual(blocked, self.partner)

    def test_notice_version_chain(self):
        consent = self._granted_consent()
        self.assertTrue(
            consent.notice_version_id,
            "consents must carry the immutable notice version",
        )
        self.assertTrue(consent.notice_version_id.hash)
