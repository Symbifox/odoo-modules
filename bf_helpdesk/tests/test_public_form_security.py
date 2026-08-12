import io

from odoo.tests import HttpCase, tagged


@tagged("bf_helpdesk", "bf_helpdesk_security", "-at_install", "post_install")
class TestPublicFormSecurity(HttpCase):
    """Defense-in-depth checks on /support/<slug>/submit:
    honeypot, email format, file extension blocklist, file size cap.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Team = cls.env["helpdesk.ticket.team"]
        cls.Ticket = cls.env["helpdesk.ticket"]
        cls.IConfig = cls.env["ir.config_parameter"].sudo()
        cls.alias = cls.env["mail.alias"].create({
            "alias_name": "sec-test",
            "alias_model_id": cls.env.ref("helpdesk_mgmt.model_helpdesk_ticket").id,
        })
        cls.team = cls.Team.create({
            "name": "Security Test Team",
            "alias_id": cls.alias.id,
            "public_form_enabled": True,
            "slug": "security-test",
        })
        # Lower attachment caps to make size-test cheap
        cls.IConfig.set_param("bf_helpdesk.public_form_max_attachment_mb", "1")
        cls.IConfig.set_param("bf_helpdesk.public_form_max_total_attachment_mb", "2")

    def _csrf(self):
        import re
        resp = self.url_open("/support/security-test")
        m = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', resp.text)
        self.assertIsNotNone(m)
        return m.group(1)

    def test_honeypot_drops_silently(self):
        token = self._csrf()
        before = self.Ticket.search_count([("team_id", "=", self.team.id)])
        resp = self.url_open(
            "/support/security-test/submit",
            data={
                "csrf_token": token,
                "name": "Bot",
                "email": "bot@example.com",
                "subject": "spam",
                "description": "buy viagra",
                "website": "http://malicious.test",  # honeypot
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Merci, c'est reçu", resp.text)
        self.assertEqual(
            self.Ticket.search_count([("team_id", "=", self.team.id)]),
            before,
            "Honeypot must drop the submission silently — no ticket created",
        )

    def test_invalid_email_rejected(self):
        token = self._csrf()
        before = self.Ticket.search_count([("team_id", "=", self.team.id)])
        resp = self.url_open(
            "/support/security-test/submit",
            data={
                "csrf_token": token,
                "name": "Bad Email",
                "email": "not-an-email",
                "subject": "test",
                "description": "test",
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("courriel invalide", resp.text.lower())
        self.assertEqual(
            self.Ticket.search_count([("team_id", "=", self.team.id)]),
            before,
        )

    def test_blocked_extension_rejected(self):
        token = self._csrf()
        before = self.Ticket.search_count([("team_id", "=", self.team.id)])
        resp = self.url_open(
            "/support/security-test/submit",
            data={
                "csrf_token": token,
                "name": "Attacker",
                "email": "atk@example.com",
                "subject": "test",
                "description": "test",
            },
            files={"attachment": ("payload.exe", b"MZ\x90\x00", "application/octet-stream")},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("non autorisé", resp.text.lower())
        self.assertEqual(
            self.Ticket.search_count([("team_id", "=", self.team.id)]),
            before,
            "No ticket may be created when blocked extension is uploaded",
        )

    def test_oversized_attachment_rejected(self):
        token = self._csrf()
        before = self.Ticket.search_count([("team_id", "=", self.team.id)])
        # Per-file cap is 1 MB; send 1.5 MB
        big = b"A" * int(1.5 * 1024 * 1024)
        resp = self.url_open(
            "/support/security-test/submit",
            data={
                "csrf_token": token,
                "name": "Big",
                "email": "big@example.com",
                "subject": "test",
                "description": "test",
            },
            files={"attachment": ("big.bin", big, "application/octet-stream")},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("trop volumineux", resp.text.lower())
        self.assertEqual(
            self.Ticket.search_count([("team_id", "=", self.team.id)]),
            before,
        )

    def test_safe_attachment_accepted(self):
        token = self._csrf()
        before = self.Ticket.search_count([("team_id", "=", self.team.id)])
        resp = self.url_open(
            "/support/security-test/submit",
            data={
                "csrf_token": token,
                "name": "Good",
                "email": "good@example.com",
                "subject": "test",
                "description": "test",
            },
            files={"attachment": ("ok.txt", b"hello", "text/plain")},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            self.Ticket.search_count([("team_id", "=", self.team.id)]),
            before + 1,
        )
