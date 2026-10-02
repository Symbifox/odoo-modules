import re

from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestMailOneLanguage(TransactionCase):
    """A consent email speaks one language, the tenant's colours and address
    (2026-09-27)."""

    def setUp(self):
        super().setUp()
        self.env["res.lang"]._activate_lang("fr_CA")
        self.env["ir.config_parameter"].sudo().set_param(
            "privacy_consent.privacy_officer_email", "rprp@ecole.example.invalid")
        partner = self.env["res.partner"].create({"name": "Élève Langue", "email": "langue@example.invalid"})
        purpose = self.env["privacy.purpose"].search([], limit=1)
        self.consent = self.env["privacy.consent"].create({
            "subject_partner_id": partner.id, "purpose_id": purpose.id})

    def _render(self, xmlid, lang):
        template = self.env.ref(xmlid).with_context(privacy_contact_lang=lang, lang=lang)
        body = template._render_field("body_html", self.consent.ids, compute_lang=True)[self.consent.id]
        subject = template._render_field("subject", self.consent.ids, compute_lang=True)[self.consent.id]
        return subject, re.sub(r"<[^>]+>", " ", body), body

    def test_each_email_in_one_language(self):
        for xmlid in ("privacy_consent.mail_template_consent_request",
                      "privacy_consent.mail_template_consent_expiring",
                      "privacy_consent.mail_template_consent_reminder_1",
                      "privacy_consent.mail_template_consent_reminder_2",
                      "privacy_consent.mail_template_consent_renewal_confirmation",
                      "privacy_consent.mail_template_consent_granted_confirmation"):
            subject, text, html = self._render(xmlid, "en_US")
            self.assertIn("Hello", text, xmlid)
            self.assertNotIn("Bonjour", text, xmlid)
            self.assertNotIn(" / ", subject, xmlid)
            subject, text, html = self._render(xmlid, "fr_CA")
            self.assertIn("Bonjour", text, xmlid)
            self.assertNotIn("Hello", text, xmlid)
            self.assertNotIn("32373c", html, xmlid)
            self.assertNotIn("privacy@example.com", html, xmlid)

    def test_officer_address_and_absolute_link(self):
        subject, text, html = self._render("privacy_consent.mail_template_consent_request", "fr_CA")
        self.assertIn("rprp@ecole.example.invalid", html)
        self.assertIn("%s/privacy/consent/%s/" % (self.consent.get_base_url(), self.consent.id)
                      if "website" not in self.env else "/privacy/consent/%s/" % self.consent.id, html)

    def test_english_fallbacks_are_english(self):
        """Without an expiry date or a plain-language summary, the English branch
        printed a French fallback."""
        self.consent.purpose_id.plain_language_summary = False
        self.assertFalse(self.consent.expires_at)
        for xmlid, attendu, francais in (
                ("privacy_consent.mail_template_consent_request",
                 "No description available.", "Aucune description disponible."),
                ("privacy_consent.mail_template_consent_expiring", "Soon", "Bientôt"),
                ("privacy_consent.mail_template_consent_renewal_confirmation",
                 "No expiry", "Aucune expiration"),
                ("privacy_consent.mail_template_consent_granted_confirmation",
                 "No expiry", "Aucune expiration")):
            subject, text, html = self._render(xmlid, "en_US")
            self.assertIn(attendu, text, xmlid)
            self.assertNotIn(francais, text, xmlid)
