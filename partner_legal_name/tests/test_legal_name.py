"""Le nom légal : sur la fiche, sur la société, et dans l'en-tête des documents."""

from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class LegalNameCase(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env["res.company"].create({"name": "Nom Commercial Essai"})

    def test_company_and_partner_share_the_legal_name(self):
        self.company.legal_name = "Raison Sociale Essai inc."
        self.assertEqual(self.company.partner_id.legal_name,
                         "Raison Sociale Essai inc.")
        self.company.partner_id.legal_name = "Autre Raison Sociale inc."
        self.assertEqual(self.company.legal_name, "Autre Raison Sociale inc.")

    def test_display_name_is_the_fallback(self):
        partner = self.company.partner_id
        self.assertEqual(partner.legal_or_display_name, "Nom Commercial Essai")
        partner.legal_name = "Raison Sociale Essai inc."
        self.assertEqual(partner.legal_or_display_name, "Raison Sociale Essai inc.")

    def test_document_layout_wizard_reads_the_legal_name(self):
        """Sans le champ lié sur l'assistant, ouvrir « Configurer la mise en
        page » levait AttributeError dans l'en-tête hérité."""
        self.company.legal_name = "Raison Sociale Essai inc."
        wizard = self.env["base.document.layout"].with_company(
            self.company).create({"company_id": self.company.id})
        self.assertEqual(wizard.legal_name, "Raison Sociale Essai inc.")
