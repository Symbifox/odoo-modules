"""Brand resolution details and the per-employee drop-page provisioning.

Two things are load-bearing here. First, the visual cascade: a brand that
resolves the wrong logo or the wrong share base URL sends clients a link on the
wrong domain — silently, because nothing errors. Second, the auto-provisioning
in ``res_users``: it creates a PUBLIC page per employee, so its gating decides
who gets an internet-facing upload endpoint. A service account slipping through
would publish a drop page for a bot.
"""
from unittest.mock import patch

from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestBrandProvisioning(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Brand = cls.env["secure.transfer.brand"]
        cls.icp = cls.env["ir.config_parameter"].sudo()

    # ------------------------------------------------------------------ slugs
    def test_slugify_folds_accents_and_case(self):
        """Slugs land in public URLs; an accented or upper-case slug would
        produce a link that does not survive copy-paste."""
        self.assertEqual(self.Brand._st_slugify("Jane Doe"), "jane-doe")
        self.assertEqual(self.Brand._st_slugify("Élise Côté"), "elise-cote")
        self.assertEqual(self.Brand._st_slugify("  A--B  "), "a-b")

    def test_slugify_empty_input_yields_empty(self):
        """No slug is better than a bogus one — the caller decides the fallback."""
        self.assertEqual(self.Brand._st_slugify("!!!"), "")
        self.assertEqual(self.Brand._st_slugify(""), "")

    def test_unique_slug_suffixes_on_collision(self):
        """Two employees with the same name must not fight over one URL."""
        base = self.Brand._st_slugify("Jean Tremblay")
        self.Brand.create({
            "name": "Jean 1", "slug": base, "is_default": False,
            "fixed_recipient": "jean1@example.com",
        })
        self.assertEqual(self.Brand._st_unique_slug("Jean Tremblay"), base + "-2")

    def test_unique_slug_accounts_for_archived_brands(self):
        """An archived brand still holds its slug (the SQL constraint does not
        care about active), so reusing it would raise on create."""
        base = self.Brand._st_slugify("Ana Silva")
        b = self.Brand.create({
            "name": "Ana", "slug": base, "is_default": False,
            "fixed_recipient": "ana@example.com",
        })
        b.active = False
        self.assertNotEqual(self.Brand._st_unique_slug("Ana Silva"), base)

    def test_slug_of_an_archived_brand_does_not_resolve(self):
        """Archiving a drop page must actually take it off the internet."""
        b = self.Brand.create({
            "name": "Temp", "slug": "temp-page", "is_default": False,
            "fixed_recipient": "temp@example.com",
        })
        self.assertTrue(self.Brand._resolve_for_slug("temp-page"))
        b.active = False
        self.assertFalse(self.Brand._resolve_for_slug("temp-page"))

    # ------------------------------------------------------------------ share base URL
    def test_share_base_url_prefers_the_brand_domain(self):
        """The link a client receives must ride the brand's own domain, never
        the backend's web.base.url."""
        b = self.Brand.create({
            "name": "Domained", "slug": "domained", "is_default": False,
            "domain": "envoi.exemple.test", "fixed_recipient": "d@example.com",
        })
        self.assertIn("envoi.exemple.test", b._share_base_url())

    def test_share_base_url_falls_back_to_public_base_url(self):
        """Brands without their own domain ride the tenant's public URL."""
        self.icp.set_param("bf_securetransfer.public_base_url",
                           "https://public.exemple.test")
        self.addCleanup(self.icp.set_param,
                        "bf_securetransfer.public_base_url", "")
        b = self.Brand.create({
            "name": "NoDomain", "slug": "nodomain", "is_default": False,
            "fixed_recipient": "n@example.com",
        })
        self.assertIn("public.exemple.test", b._share_base_url())

    def test_share_base_url_adds_scheme_and_trims_slash(self):
        """A base URL typed without a scheme, or with a trailing slash, would
        produce 'exemple.test/s/token' or a double slash in the link."""
        self.icp.set_param("bf_securetransfer.public_base_url",
                           "exemple.test/")
        self.addCleanup(self.icp.set_param,
                        "bf_securetransfer.public_base_url", "")
        b = self.Brand.create({
            "name": "Sloppy", "slug": "sloppy", "is_default": False,
            "fixed_recipient": "s@example.com",
        })
        url = b._share_base_url()
        self.assertTrue(url.startswith("https://"))
        self.assertFalse(url.endswith("/"))

    def test_page_url_is_empty_without_a_slug(self):
        """The form hides the field on falsy page_url; a stray value would
        advertise a page that 404s."""
        b = self.Brand.create({"name": "Slugless", "is_default": False})
        self.assertFalse(b.page_url)

    def test_orphan_net_counts_archived_brands(self):
        """The provider-side lifecycle net must clear the longest retention any
        brand ever granted — including one archived yesterday, because
        archiving a brand does not expire the transfers it already handed out.
        Missing this would let the provider delete a live 105-day transfer."""
        from odoo.addons.bf_securetransfer.models import s3
        b = self.Brand.create({
            "name": "Long retention", "is_default": False,
            "slug": "long-retention", "fixed_recipient": "lr@example.com",
            "max_retention_days": 200,
        })
        with_active = s3._orphan_expiry_days(self.env)
        b.active = False
        self.assertEqual(
            s3._orphan_expiry_days(self.env), with_active,
            "archiving a brand must not shrink the safety net")
        self.assertGreaterEqual(with_active, 200 + 15)

    # ------------------------------------------------------------------ visual cascade
    def test_visuals_fall_back_to_the_house_colour(self):
        """With nothing set anywhere, the pages must still render in the Blue
        Fox blue rather than an empty CSS value."""
        b = self.Brand.create({"name": "Bare", "is_default": False})
        v = b._visuals()
        self.assertTrue(v.get("primary"))
        self.assertTrue(v.get("dark"))
        self.assertTrue(v.get("name"))

    def test_visuals_prefer_the_brand_colour_over_the_company(self):
        """An explicit per-brand colour is the whole point of white-labelling."""
        b = self.Brand.create({
            "name": "Coloured", "is_default": False,
            "color_primary": "#123456",
        })
        self.assertEqual(b._visuals()["primary"], "#123456")

    # ------------------------------------------------------------------ provisioning gate
    def _mk_user(self, login, **vals):
        base = {
            "name": login, "login": login, "email": "%s@example.com" % login,
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id])],
        }
        base.update(vals)
        return self.env["res.users"].create(base)

    def test_autoprovision_is_off_unless_explicitly_enabled(self):
        """This flag publishes an internet-facing page per employee. Anything
        other than an explicit opt-in must leave it off."""
        for value, expected in (("1", True), ("True", True), ("true", True),
                                ("0", False), ("", False), ("yes", False)):
            self.icp.set_param(
                "bf_securetransfer.autoprovision_user_pages", value)
            self.assertEqual(
                self.env["res.users"]._st_autoprovision_enabled(), expected,
                "value %r must map to %s" % (value, expected))
        self.icp.set_param("bf_securetransfer.autoprovision_user_pages", "0")

    def test_service_accounts_never_get_a_page(self):
        """A drop page for odoobot or an API account is a public endpoint
        nobody owns."""
        Users = self.env["res.users"]
        self.assertTrue(Users.browse(1)._st_is_serviceish())
        for login in ("odoobot", "api@example.com", "n8n-runner",
                      "noreply@example.com", "sync-bot"):
            u = self._mk_user(login)
            self.assertTrue(u._st_is_serviceish(),
                            "%s should read as a service account" % login)

    def test_portal_user_gets_no_page(self):
        """Portal users are clients, not staff — they must not receive an
        upload endpoint on the company's domain."""
        u = self._mk_user("st-portal-candidate")
        u.share = True
        self.assertFalse(u._st_should_have_page())

    def test_opted_out_and_emailless_users_get_no_page(self):
        """Opt-out must be honoured, and a page whose fixed recipient is empty
        would collect files nobody receives."""
        u = self._mk_user("st-optout")
        u.st_no_drop_page = True
        self.assertFalse(u._st_should_have_page())

        u2 = self._mk_user("st-noemail")
        u2.email = False
        self.assertFalse(u2._st_should_have_page())

    def test_sync_creates_updates_and_archives(self):
        """The full provisioning lifecycle: one page created, renamed on a
        name change, and archived — never deleted — when the user leaves, so
        the Loi 25 trail of past transfers survives."""
        u = self._mk_user("st-provision")
        u._st_sync_drop_page()
        brand = self.Brand.with_context(active_test=False).search(
            [("owner_user_id", "=", u.id)])
        self.assertEqual(len(brand), 1)
        self.assertEqual(brand.fixed_recipient, "st-provision@example.com")
        self.assertEqual(brand.tier, "paid")

        u.name = "Renommé"
        u._st_sync_drop_page()
        brand.invalidate_recordset()
        self.assertIn("Renommé", brand.name)

        u.active = False
        u._st_sync_drop_page()
        brand.invalidate_recordset()
        self.assertFalse(brand.active, "the page must be archived, not deleted")
        self.assertTrue(brand.exists(), "history must survive")

    def test_sync_is_idempotent_and_creates_no_duplicate(self):
        """Repeated writes on a user must not accumulate pages."""
        u = self._mk_user("st-idem")
        for _i in range(3):
            u._st_sync_drop_page()
        found = self.Brand.with_context(active_test=False).search(
            [("owner_user_id", "=", u.id)])
        self.assertEqual(len(found), 1)

    def test_sync_failure_never_blocks_user_write(self):
        """Provisioning is a convenience. If it breaks, creating or editing an
        employee must still work — HR does not care about drop pages."""
        u = self._mk_user("st-resilient")
        with patch.object(type(self.env["secure.transfer.brand"]), "create",
                          side_effect=Exception("boom")):
            try:
                u._st_sync_drop_page()
            except Exception as exc:  # pragma: no cover - guard assertion
                self.fail("a provisioning error must be swallowed: %s" % exc)
