from odoo.tests import HttpCase, tagged


@tagged("bf_helpdesk", "bf_helpdesk_public_form", "-at_install", "post_install")
class TestPublicForm(HttpCase):
    """End-to-end HTTP tests for /support/<slug>."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Team = cls.env["helpdesk.ticket.team"]
        cls.Ticket = cls.env["helpdesk.ticket"]
        cls.alias_on = cls.env["mail.alias"].create({
            "alias_name": "pf-on",
            "alias_model_id": cls.env.ref("helpdesk_mgmt.model_helpdesk_ticket").id,
        })
        cls.team_on = cls.Team.create({
            "name": "Public Form ON Team",
            "alias_id": cls.alias_on.id,
            "public_form_enabled": True,
            "slug": "public-form-on",
        })
        cls.alias_off = cls.env["mail.alias"].create({
            "alias_name": "pf-off",
            "alias_model_id": cls.env.ref("helpdesk_mgmt.model_helpdesk_ticket").id,
        })
        cls.team_off = cls.Team.create({
            "name": "Public Form OFF Team",
            "alias_id": cls.alias_off.id,
            "public_form_enabled": False,
            "slug": "public-form-off",
        })

    def test_get_renders_when_enabled(self):
        resp = self.url_open("/support/public-form-on")
        self.assertEqual(resp.status_code, 200)
        body = resp.text
        self.assertIn("Public Form ON Team", body)
        self.assertIn("bf-support-page", body, "Branded BF SCSS class must be present")
        self.assertIn("bf-support-form", body)
        self.assertIn("csrf_token", body, "CSRF token must be in the form")

    def test_get_404_when_disabled(self):
        resp = self.url_open("/support/public-form-off")
        self.assertEqual(resp.status_code, 404)

    def test_get_404_unknown_slug(self):
        resp = self.url_open("/support/does-not-exist")
        self.assertEqual(resp.status_code, 404)

    def test_post_creates_ticket(self):
        # Get CSRF token first
        resp = self.url_open("/support/public-form-on")
        self.assertEqual(resp.status_code, 200)
        import re
        m = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', resp.text)
        self.assertIsNotNone(m, "CSRF token must be retrievable")
        token = m.group(1)

        before_count = self.Ticket.search_count([("team_id", "=", self.team_on.id)])

        resp2 = self.url_open(
            "/support/public-form-on/submit",
            data={
                "csrf_token": token,
                "name": "Test Submitter",
                "email": "submitter@test.invalid",
                "subject": "QA test ticket",
                "description": "Test description",
            },
            allow_redirects=True,
        )
        self.assertEqual(resp2.status_code, 200)

        ticket = self.Ticket.search([
            ("team_id", "=", self.team_on.id),
            ("name", "=", "QA test ticket"),
        ], limit=1)
        self.assertTrue(ticket, "Ticket must be created from public form submission")
        self.assertEqual(ticket.partner_email, "submitter@test.invalid")
        self.assertEqual(ticket.partner_name, "Test Submitter")
        self.assertEqual(
            self.Ticket.search_count([("team_id", "=", self.team_on.id)]),
            before_count + 1,
        )

    def test_post_rejects_missing_required(self):
        resp = self.url_open("/support/public-form-on")
        import re
        m = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', resp.text)
        token = m.group(1)
        before_count = self.Ticket.search_count([("team_id", "=", self.team_on.id)])
        resp2 = self.url_open(
            "/support/public-form-on/submit",
            data={
                "csrf_token": token,
                "name": "",
                "email": "",
                "subject": "",
                "description": "",
            },
        )
        self.assertEqual(resp2.status_code, 200, "Should re-render form with error, not 500")
        self.assertIn("requis", resp2.text.lower())
        self.assertEqual(
            self.Ticket.search_count([("team_id", "=", self.team_on.id)]),
            before_count,
            "No ticket may be created when required fields are missing",
        )
