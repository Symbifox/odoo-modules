"""Brand resolution by request Host: exact match, case, port,
X-Forwarded-Host chains, default fallback, single-default constraint."""
from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestBrandHostResolution(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Brand = cls.env["secure.transfer.brand"]
        cls.default = cls.env.ref("bf_securetransfer.brand_default")
        cls.brand_a = cls.Brand.create({
            "name": "Marque Secret",
            "domain": "secret.example.test",
            "tier": "paid",
        })
        cls.other = cls.Brand.create({
            "name": "Autre client",
            "domain": "envoi.client.test",
        })

    # ------------------------------------------------------------------ matching
    def test_exact_domain(self):
        self.assertEqual(
            self.Brand._resolve_for_host("secret.example.test"), self.brand_a,
        )
        self.assertEqual(
            self.Brand._resolve_for_host("envoi.client.test"), self.other,
        )

    def test_case_insensitive(self):
        self.assertEqual(
            self.Brand._resolve_for_host("SECRET.Example.TEST"), self.brand_a,
        )

    def test_port_stripped(self):
        self.assertEqual(
            self.Brand._resolve_for_host("secret.example.test:8443"),
            self.brand_a,
        )

    def test_forwarded_host_chain_keeps_first(self):
        self.assertEqual(
            self.Brand._resolve_for_host(
                "secret.example.test, proxy.interne.test"
            ),
            self.brand_a,
        )
        self.assertEqual(
            self.Brand._resolve_for_host(
                "secret.example.test:443, proxy.interne.test:8080"
            ),
            self.brand_a,
        )

    def test_whitespace_tolerated(self):
        self.assertEqual(
            self.Brand._resolve_for_host("  secret.example.test  "),
            self.brand_a,
        )

    def test_unknown_host_matches_nothing(self):
        self.assertFalse(self.Brand._resolve_for_host("inconnu.example.test"))
        self.assertFalse(self.Brand._resolve_for_host(""))
        self.assertFalse(self.Brand._resolve_for_host(None))
        self.assertFalse(self.Brand._resolve_for_host(" , "))

    def test_inactive_brand_not_resolved(self):
        self.other.active = False
        self.assertFalse(self.Brand._resolve_for_host("envoi.client.test"))

    def test_wildcard_host_does_not_resolve(self):
        """A Host header is attacker-controlled and "=ilike" treats it as a
        LIKE pattern. Anything carrying a metacharacter must resolve to
        nothing — otherwise "Host: %" hands over the first branded tenant,
        including its email_from."""
        self.assertFalse(self.Brand._resolve_for_host("%"))
        self.assertFalse(self.Brand._resolve_for_host("%.example.test"))
        self.assertFalse(self.Brand._resolve_for_host("secret.example.tes_"))
        # the legitimate host still resolves
        self.assertEqual(
            self.Brand._resolve_for_host("envoi.client.test"), self.other)

    # ------------------------------------------------------------------ fallback
    def test_from_request_falls_back_to_default(self):
        # No HTTP request in tests -> host is empty -> default brand
        self.assertEqual(self.Brand._from_request(), self.default)

    def test_from_request_never_empty_without_default(self):
        self.default.is_default = False
        brand = self.Brand._from_request()
        self.assertTrue(brand)
        self.assertEqual(brand._name, "secure.transfer.brand")

    # ------------------------------------------------------------------ default unicity
    def test_single_active_default(self):
        with self.assertRaises(ValidationError):
            self.brand_a.is_default = True

    def test_second_default_allowed_when_first_inactive(self):
        self.default.active = False
        self.brand_a.is_default = True  # must not raise
        self.assertTrue(self.brand_a.is_default)

    # ------------------------------------------------------------------ sender allowlist
    def test_allowlist_empty_is_open(self):
        self.assertTrue(self.default._sender_allowed("anyone@example.com"))

    def test_allowlist_domain_entry(self):
        self.brand_a.sender_allowlist = "@client.test"
        self.assertTrue(self.brand_a._sender_allowed("jean@client.test"))
        self.assertTrue(self.brand_a._sender_allowed("JEAN@Client.Test"))  # case
        self.assertFalse(self.brand_a._sender_allowed("jean@autre.test"))

    def test_allowlist_bare_domain_and_full_address(self):
        self.brand_a.sender_allowlist = "client.test\nvip@ailleurs.test"
        self.assertTrue(self.brand_a._sender_allowed("a@client.test"))     # bare domain
        self.assertTrue(self.brand_a._sender_allowed("vip@ailleurs.test"))  # full addr
        self.assertFalse(self.brand_a._sender_allowed("other@ailleurs.test"))
        self.assertFalse(self.brand_a._sender_allowed("notanemail"))

    def test_allowlist_comma_separated(self):
        self.brand_a.sender_allowlist = "@a.test, @b.test"
        self.assertTrue(self.brand_a._sender_allowed("x@a.test"))
        self.assertTrue(self.brand_a._sender_allowed("y@b.test"))
        self.assertFalse(self.brand_a._sender_allowed("z@c.test"))

    # ------------------------------------------------------------------ recipient allowlist
    def test_recipient_allowlist_empty_is_open(self):
        self.assertTrue(self.default._recipient_allowed("anyone@example.com"))

    def test_recipient_allowlist_locks_to_domain(self):
        self.brand_a.recipient_allowlist = "@client.test"
        self.assertTrue(self.brand_a._recipient_allowed("bob@client.test"))
        self.assertFalse(self.brand_a._recipient_allowed("bob@gmail.test"))
        # sender and recipient lists are independent
        self.brand_a.sender_allowlist = ""
        self.assertTrue(self.brand_a._sender_allowed("anyone@x.test"))

    def test_tenant_default_allowlist_and_override(self):
        icp = self.env["ir.config_parameter"].sudo()
        # Tenant-wide default applies to a brand that has no list of its own.
        icp.set_param("bf_securetransfer.default_recipient_allowlist", "@corp.test")
        self.assertTrue(self.default._recipient_allowed("a@corp.test"))
        self.assertFalse(self.default._recipient_allowed("a@other.test"))
        # A brand's own list OVERRIDES the tenant default.
        self.brand_a.recipient_allowlist = "@client.test"
        self.assertTrue(self.brand_a._recipient_allowed("a@client.test"))
        self.assertFalse(self.brand_a._recipient_allowed("a@corp.test"))
        icp.set_param("bf_securetransfer.default_recipient_allowlist", "")

    # ------------------------------------------------------------------ bf_branding alignment
    def test_visuals_align_with_company_branding(self):
        # When unset on the brand, colours fall back to the company's
        # report_brand_* (bf_onboarding_base) if present, else the hardcoded
        # defaults. Either way _visuals must return usable values + the
        # bf_branding tagline/footer keys.
        v = self.default._visuals()
        for key in ("primary", "dark", "logo_url", "logo_host", "favicon_url",
                    "powered_by_name", "tagline", "footer_html"):
            self.assertIn(key, v)
        self.assertTrue(v["primary"].startswith("#") or v["primary"])
        # powered-by credits the operator's PUBLIC brand name
        # (appointment_brand_name) when set, else res.company.name.
        company = self.default.company_id
        self.assertEqual(
            v["powered_by_name"],
            getattr(company, "appointment_brand_name", False) or company.name,
        )
