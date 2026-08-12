from odoo.tests import TransactionCase, tagged


@tagged("privacy_framework_gdpr")
class TestGdprFramework(TransactionCase):
    """The GDPR pack must render GDPR (not Loi 25) on its records."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.gdpr = cls.env.ref("privacy_framework_gdpr.framework_gdpr")

    def test_framework_loaded(self):
        self.assertEqual(self.gdpr.law_short_label, "GDPR")
        self.assertIn("DPO", self.gdpr.officer_title_en)
        self.assertEqual(self.gdpr.age_of_majority, 16)
        self.assertEqual(self.gdpr.breach_notify_authority_hours, 72)

    def test_no_loi25_leakage(self):
        self.assertNotIn("Loi 25", self.gdpr.law_citation_fr)
        self.assertNotIn("LPRPSP", self.gdpr.destruction_basis_template)
        self.assertIn("GDPR", self.gdpr.destruction_basis_template)

    def test_six_lawful_bases(self):
        codes = set(self.gdpr.lawful_basis_ids.mapped("code"))
        self.assertEqual(
            codes,
            {"consent", "contract", "legal_obligation",
             "vital_interests", "public_task", "legitimate_interests"},
        )

    def test_rights_include_gdpr_specific(self):
        codes = set(self.gdpr.right_ids.mapped("code"))
        self.assertEqual(len(self.gdpr.right_ids), 7)
        self.assertIn("erasure", codes)
        self.assertIn("portability", codes)
        self.assertIn("object", codes)

    def test_consent_resolves_gdpr(self):
        partner = self.env["res.partner"].create({"name": "EU Subject"})
        consent = self.env["privacy.consent"].create({
            "subject_partner_id": partner.id,
            "framework_id": self.gdpr.id,
        })
        resolved = consent.get_framework()
        self.assertEqual(resolved, self.gdpr)
        self.assertEqual(resolved.law_short_label, "GDPR")
