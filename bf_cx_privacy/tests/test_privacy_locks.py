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

    def test_a_lost_consent_is_traced_in_the_company_language(self):
        """The withdrawal comes from the client's page (their
        language) or from the expiry job (none). The trace is read by staff:
        the company's language, and the status by its label, not its key."""
        from odoo.addons.bf_cx.tests.common import amorcer_francais
        amorcer_francais(self.env, ("bf_cx", "bf_cx_privacy"))
        consent = self._granted_consent()
        self.testimonial.privacy_consent_id = consent
        self.testimonial.action_check_privacy_consent()
        consent.with_context(lang="en_US").write({"status": "withdrawn"})
        trace = self.testimonial.message_ids.filtered(
            lambda m: "Loi 25" in (m.body or "") and "ne peut plus" in (m.body or ""))
        self.assertTrue(trace, "no French trace of the lost consent")
        self.assertNotIn("« withdrawn »", trace[0].body)

    def test_the_purpose_and_the_notice_keep_their_own_names(self):
        """privacy_consent renames a notice's purpose when the notice is
        renamed: the 18.0.1.1.1 migration must not let the notice's name
        overwrite the purpose's (seen in rehearsal)."""
        from odoo.addons.bf_cx.tests.common import amorcer_francais
        amorcer_francais(self.env, ("bf_cx", "bf_cx_privacy"))
        purpose = self.env.ref("bf_cx_privacy.purpose_testimonial")
        for lang, nom_finalite, nom_avis in (
                ("en_US", "Publication of a client testimonial", "Use of your testimonial"),
                ("fr_CA", "Publication d'un témoignage client", "Utilisation de votre témoignage")):
            self.assertEqual(purpose.with_context(lang=lang).name, nom_finalite)
            self.assertEqual(self.notice.with_context(lang=lang).name, nom_avis)
