"""Brand resolution by request Host: exact match, case, port,
X-Forwarded-Host chains, default fallback, single-default constraint."""
import base64
import io

from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged

from .common import BaseNeuve


def _png():
    """Un PNG 1x1 réel, FABRIQUÉ et non recopié.

    ⚠️ Une constante base64 écrite à la main avait un octet de décalage : PIL
    lisait un nom de bloc «  IEN » et l'écriture du champ Binary levait, loin
    de ce que l'essai vérifie.
    """
    from PIL import Image
    tampon = io.BytesIO()
    Image.new("RGB", (1, 1), (41, 171, 226)).save(tampon, format="PNG")
    return tampon.getvalue()


@tagged("post_install", "-at_install")
class TestBrandHostResolution(BaseNeuve, TransactionCase):

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
        # « Powered by » credits the brand's own name, never the company
        # served: see test_propulse_par.
        self.assertEqual(v["powered_by_name"],
                         (self.default.powered_by_name or "").strip() or "Symbifox")

    def test_le_logo_est_servi_sur_la_meme_origine_que_la_page(self):
        """Si ça casse : le logo de la page publique repointe le domaine de la
        marque, et toute page servie ailleurs (environnement d'essai, domaine pas encore
        branché, `public_base_url` différent) montre une image brisée — le
        défaut mesuré le 2026-10-10 sur la page Boréal.

        Les courriels, eux, ont besoin d'absolu : ils préfixent déjà par
        `_share_base_url()` toute adresse qui ne commence pas par http (voir
        test_emails_i18n), donc la forme relative leur convient.
        """
        self.default.sudo().write({
            "logo": base64.b64encode(_png()),
            "domain": "envoi.marque-essai.test",
        })
        url = self.default._visuals()["logo_url"]
        self.assertTrue(url.startswith("/web/image/"), url)
        self.assertNotIn("marque-essai.test", url)
        # Rien à inscrire dans la CSP : l'image vient de chez nous.
        self.assertFalse(self.default._visuals()["logo_host"])

    def test_un_logo_hors_de_chez_nous_reste_absolu(self):
        """Un logo public hébergé ailleurs (appointment_brand_logo_url) garde
        son adresse entière, et son hôte reste exposé pour la CSP."""
        company = self.default.company_id
        if "appointment_brand_logo_url" not in company._fields:
            self.skipTest("bf_appointment absent")
        self.default.sudo().logo = False
        vals = {"appointment_brand_logo_url": "https://cdn.ailleurs.test/l.png"}
        if "report_brand_logo" in company._fields:
            vals["report_brand_logo"] = False
        company.sudo().write(vals)
        v = self.default._visuals()
        self.assertEqual(v["logo_url"], "https://cdn.ailleurs.test/l.png")
        self.assertEqual(v["logo_host"], "https://cdn.ailleurs.test")


@tagged("post_install", "-at_install")
class TestBrandFromRequestBehindProxy(TransactionCase):
    """La marque suit l'hôte qu'Odoo retient, pas le 1er élément brut.

    Avec `proxy_mode`, ProxyFix garde le DERNIER X-Forwarded-Host (celui de
    notre mandataire) dans `host`. Lire l'en-tête brut et garder son premier
    élément laissait le client choisir la marque en se plaçant devant.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Brand = cls.env["secure.transfer.brand"]
        cls.marque = Brand.create({"name": "Marque Secret",
                                   "domain": "secret.example.test"})
        cls.other = Brand.create({"name": "Autre client",
                                  "domain": "envoi.client.test"})

    def _from(self, host, forwarded, proxy_mode):
        from types import SimpleNamespace
        from unittest.mock import patch

        from odoo.tools import config
        fake = SimpleNamespace(httprequest=SimpleNamespace(
            host=host, headers={"X-Forwarded-Host": forwarded}))
        mod = "odoo.addons.bf_securetransfer.models.secure_transfer_brand"
        with patch(mod + ".request", fake), \
                patch.dict(config.options, {"proxy_mode": proxy_mode}):
            return self.env["secure.transfer.brand"]._from_request()

    def test_client_devant_le_mandataire_avec_proxy_mode(self):
        # ProxyFix a déjà mis le dernier saut dans `host`.
        self.assertEqual(self._from(
            "secret.example.test",
            "envoi.client.test, secret.example.test", True), self.marque)

    def test_sans_proxy_mode_dernier_saut(self):
        self.assertEqual(self._from(
            "127.0.0.1:8069",
            "envoi.client.test, secret.example.test", False), self.marque)
