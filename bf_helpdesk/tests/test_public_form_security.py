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

    def test_submit_is_rate_limited_per_ip(self):
        # After _SUBMIT_MAX accepted submissions from one IP within the window,
        # further submissions must not create tickets (anti spam / mail-bomb).
        from odoo.addons.bf_helpdesk.controllers import public_form
        public_form._submit_data.clear()
        before = self.Ticket.search_count([("team_id", "=", self.team.id)])
        created = 0
        for i in range(public_form._SUBMIT_MAX + 3):
            token = self._csrf()
            self.url_open(
                "/support/security-test/submit",
                data={
                    "csrf_token": token,
                    "name": "U%d" % i,
                    "email": "u%d@example.com" % i,
                    "subject": "s%d" % i,
                    "description": "d%d" % i,
                },
            )
        after = self.Ticket.search_count([("team_id", "=", self.team.id)])
        created = after - before
        self.assertEqual(
            created, public_form._SUBMIT_MAX,
            "only the first _SUBMIT_MAX submissions per IP should create tickets",
        )
        public_form._submit_data.clear()

    def test_browser_renderable_extensions_rejected(self):
        # svg/html/xml attachments render inline in the agent's backend origin
        # when opened → stored XSS. They must be blocked like .exe.
        for fname, payload in (
            ("payload.svg", b"<svg xmlns='http://www.w3.org/2000/svg'>"
                            b"<script>alert(1)</script></svg>"),
            ("payload.html", b"<html><script>alert(1)</script></html>"),
            ("payload.xml", b"<?xml version='1.0'?><x/>"),
        ):
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
                files={"attachment": (fname, payload, "image/svg+xml")},
            )
            self.assertEqual(resp.status_code, 200)
            self.assertIn("non autorisé", resp.text.lower())
            self.assertEqual(
                self.Ticket.search_count([("team_id", "=", self.team.id)]),
                before,
                "%s must be rejected (stored-XSS vector)" % fname,
            )

    def test_accepted_attachment_gets_server_derived_mimetype(self):
        # A benign upload whose client Content-Type lies must be stored with a
        # server-derived mimetype (never the client's) so it can't be served
        # inline as html/svg.
        token = self._csrf()
        resp = self.url_open(
            "/support/security-test/submit",
            data={
                "csrf_token": token,
                "name": "Good",
                "email": "mime@example.com",
                "subject": "mime",
                "description": "mime",
            },
            # Client claims html for a .txt payload; server must store text/plain.
            files={"attachment": ("note.txt", b"hello", "text/html")},
        )
        self.assertEqual(resp.status_code, 200)
        ticket = self.Ticket.search(
            [("team_id", "=", self.team.id), ("partner_email", "=", "mime@example.com")],
            limit=1, order="id desc",
        )
        att = self.env["ir.attachment"].search([
            ("res_model", "=", "helpdesk.ticket"), ("res_id", "=", ticket.id),
        ], limit=1)
        self.assertTrue(att)
        self.assertNotEqual(att.mimetype, "text/html")
        self.assertEqual(att.mimetype, "text/plain")
