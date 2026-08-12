from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged("privacy_consent", "privacy_framework")
class TestPrivacyFramework(TransactionCase):
    """Multi-framework abstraction: Loi 25 baseline must be unchanged."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.loi25 = cls.env.ref("privacy_consent.framework_loi25")
        cls.Register = cls.env["privacy.destruction.register"]
        cls.officer = cls.env["res.users"].create({
            "name": "FW Officer",
            "login": "test_fw_officer",
            "email": "fw_officer@example.com",
            "groups_id": [(4, cls.env.ref("privacy_consent.group_privacy_officer").id)],
        })

    def test_loi25_framework_loaded(self):
        self.assertEqual(self.loi25.law_short_label, "Loi 25")
        self.assertIn("RPRP", self.loi25.officer_title_fr)
        self.assertIn("P-39.1", self.loi25.law_citation_fr)
        self.assertEqual(self.loi25.age_of_majority, 14)

    def test_loi25_destruction_citations_byte_identical(self):
        """Exact strings preserve register legal_basis (and thus the hash)."""
        self.assertEqual(
            self.loi25.destruction_basis_template,
            "Art. 23 LPRPSP (obligation de destruction)",
        )
        self.assertEqual(
            self.loi25.erasure_basis_citation,
            "Art. 28.1 LPRPSP (droit à l'effacement)",
        )
        self.assertEqual(
            self.loi25.register_immutability_citation,
            "l'article 3.2 de la Loi sur la protection des renseignements "
            "personnels dans le secteur privé (LPRPSP)",
        )

    def test_loi25_access_rectify_right(self):
        right = self.env.ref("privacy_consent.right_loi25_access_rectify")
        self.assertEqual(right.framework_id, self.loi25)
        self.assertEqual(
            right.label_fr,
            "Vous avez le droit d'accéder à vos renseignements personnels et de les rectifier.",
        )
        self.assertEqual(
            right.label_en,
            "You have the right to access and rectify your personal information.",
        )

    def test_company_default_is_loi25(self):
        """post_init_hook wires the company default to Loi 25."""
        self.assertEqual(self.env.company.default_privacy_framework_id, self.loi25)

    def test_new_record_defaults_to_company_framework(self):
        purpose = self.env["privacy.purpose"].create({
            "name": "FW default", "code": "test_fw_default",
        })
        self.assertEqual(purpose.framework_id, self.loi25)
        self.assertEqual(purpose.get_framework(), self.loi25)

    def test_resolve_falls_back_to_company_default(self):
        purpose = self.env["privacy.purpose"].create({
            "name": "FW fallback", "code": "test_fw_fallback", "framework_id": False,
        })
        self.assertFalse(purpose.framework_id)
        self.assertEqual(
            purpose.get_framework(),
            self.env.company.default_privacy_framework_id,
        )

    def test_register_snapshots_framework_and_immutability(self):
        entry = self.Register.create({
            "destroyed_by_id": self.officer.id,
            "approved_by_id": self.officer.id,
            "document_description": "FW test",
            "destruction_method": "delete",
            "legal_basis": "Test",
        })
        self.assertEqual(entry.framework_id, self.loi25)
        self.assertEqual(
            entry._register_immutability_citation(),
            self.loi25.register_immutability_citation,
        )
        with self.assertRaises(UserError):
            entry.write({"legal_basis": "tampered"})
        # Chain still intact after the new (framework-aware) entry.
        self.assertEqual(self.Register.cron_verify_chain_integrity(), 0)
