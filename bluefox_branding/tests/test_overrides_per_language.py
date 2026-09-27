from odoo.tests import TransactionCase, tagged

from odoo.addons.bluefox_branding.hooks import _extract_templates_from_xml, post_init_hook


@tagged("post_install", "-at_install")
class TestOverridesPerLanguage(TransactionCase):
    """The overridden standard templates speak the reader's language (2026-09-27)."""

    def test_both_files_cover_the_same_records(self):
        fr, en = _extract_templates_from_xml(), _extract_templates_from_xml("mail_template_overrides_en.xml")
        self.assertEqual(set(fr), set(en))
        for xml_id in fr:
            self.assertEqual(set(fr[xml_id]), set(en[xml_id]), xml_id)

    def test_english_and_french_versions(self):
        self.env["res.lang"]._activate_lang("fr_CA")
        post_init_hook(self.env)
        template = self.env.ref("portal.mail_template_data_portal_welcome")
        self.assertIn("Your access", template.with_context(lang="en_US").subject)
        self.assertIn("Votre accès", template.with_context(lang="fr_CA").subject)
        body_en = template.with_context(lang="en_US").body_html
        self.assertIn("Activate my account", body_en)
        self.assertNotIn("Bonjour", body_en)
        self.assertNotIn("mandat", template.with_context(lang="fr_CA").body_html,
                         "the invitation is neutral: schools are not consulting mandates")

    def test_invoices_take_the_branded_layout(self):
        self.assertEqual(self.env["account.move.send"]._get_mail_layout(),
                         "bluefox_branding.bf_mail_layout_with_signature")
