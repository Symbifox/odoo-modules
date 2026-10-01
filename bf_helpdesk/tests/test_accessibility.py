"""Accessibilité du portail et du formulaire public (cible SGQRI 008 3.0).

Mesuré au navigateur avec axe-core (WCAG 2.1 AA et 2.2 AA) ; ces essais
gardent les correctifs de ce qui a été trouvé.
"""
import re

from odoo.tests import HttpCase, new_test_user, tagged


@tagged("post_install", "-at_install", "bf_helpdesk", "bf_helpdesk_a11y")
class TestAccessibility(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        model = cls.env.ref("helpdesk_mgmt.model_helpdesk_ticket")
        cls.team = cls.env["helpdesk.ticket.team"].create({
            "name": "A11y", "public_form_enabled": True, "slug": "a11y",
            "ack_channel_ids": [(5, 0, 0)],
            "alias_id": cls.env["mail.alias"].create({
                "alias_name": "a11y-essai", "alias_model_id": model.id,
            }).id,
        })
        cls.env.company.report_brand_primary = "#29ABE2"

    def setUp(self):
        super().setUp()
        from odoo.addons.bf_helpdesk.controllers import public_form
        public_form._submit_data.clear()

    def test_brand_variables_are_readable(self):
        html = self.url_open("/support/a11y").text
        self.assertIn("--bf-hd-brand: #29ABE2", html)
        # Texte foncé sur le bleu clair : 4,5:1 tenu, contrairement au blanc.
        self.assertIn("--bf-hd-on-brand: #1F2328", html)

    def test_error_takes_focus(self):
        html = self.url_open("/support/a11y").text
        token = re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)
        html = self.url_open("/support/a11y/submit", data={
            "csrf_token": token, "email": "pas-un-courriel",
            "subject": "x", "description": "x",
        }).text
        self.assertRegex(html, r'id="bf_support_error"[^>]*autofocus')
        self.assertIn('aria-hidden="true">*', html)

    def test_portal_labels_and_button_names(self):
        new_test_user(self.env, login="a11y-client", groups="base.group_portal")
        self.authenticate("a11y-client", "a11y-client")
        html = self.url_open("/new/ticket").text
        self.assertRegex(html, r'<input[^>]*name="subject"[^>]*id="subject"|<input[^>]*id="subject"[^>]*name="subject"')
        self.assertIn('id="description"', html)
        html = self.url_open("/my/tickets").text
        self.assertIn('aria-label="Rechercher"', html)
        self.assertIn('aria-label="Choisir où chercher"', html)
