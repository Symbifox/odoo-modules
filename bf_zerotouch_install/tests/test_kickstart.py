"""Tests for the zero-touch kickstart rendered from bf.policy.org.

Since 18.0.4.0.0 this module owns no model: per-tenant config lives in
bf.policy.org and the controller renders the kickstart from org._kickstart_vars().
These tests exercise that helper + the host routing + the full template render.
"""

from odoo.tests import TransactionCase, tagged

from odoo.addons.bf_zerotouch_install.controllers.main import (
    _PLACEHOLDER_RE,
    _render_kickstart,
)

# Placeholders the controller injects at render time, not from the org.
_CONTROLLER_INJECTED = {"GENERATED_AT", "PROVISION_SCRIPT", "APPLY_SCRIPT"}


@tagged("post_install", "-at_install")
class TestKickstart(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Org = cls.env["bf.policy.org"]
        # A second tenant in its own company, fully configured for zero-touch.
        cls.acme_company = cls.env["res.company"].create({"name": "Acme Co."})
        cls.acme = cls.Org.create({
            "company_id": cls.acme_company.id,
            "domain": "acme.example",
            "slug": "acme",
            "oci_image_ref": "ghcr.io/acme/blue-fox-os-acme:latest",
            "addsupport": "en_US.UTF-8",
            "keyboard_x": "'us'",
            "authentik_device_url": "https://auth.acme.example/application/o/device/",
            "authentik_token_url": "https://auth.acme.example/application/o/token/",
            "oidc_client_id": "blue-fox-os",
        })

    # ----------------------------------------------------------------- helpers
    def test_kickstart_vars_complete(self):
        v = self.acme._kickstart_vars()
        # Every placeholder the template needs (minus controller-injected ones)
        # must be produced by the org.
        with open(
            _render_kickstart.__globals__["_TEMPLATE_PATH"], encoding="utf-8"
        ) as f:
            needed = set(_PLACEHOLDER_RE.findall(f.read())) - _CONTROLLER_INJECTED
        self.assertTrue(needed.issubset(set(v)),
                        f"missing kickstart vars: {needed - set(v)}")
        self.assertEqual(v["TENANT_SLUG"], "acme")
        self.assertEqual(v["OCI_IMAGE_REF"], "ghcr.io/acme/blue-fox-os-acme:latest")

    def test_policy_url_derived_from_domain(self):
        self.assertEqual(self.acme._policy_url(),
                         "https://acme.example/api/v1/policy/me")

    def test_policy_url_strips_www(self):
        self.acme.domain = "www.acme.example"
        self.assertEqual(self.acme._policy_url(),
                         "https://acme.example/api/v1/policy/me")

    def test_slug_falls_back_to_domain_label(self):
        self.acme.slug = False
        self.assertEqual(self.acme._kickstart_vars()["TENANT_SLUG"], "acme")

    def test_blank_image_disables(self):
        # No image => the var is empty; the controller turns this into a 404.
        self.acme.oci_image_ref = False
        self.assertEqual(self.acme._kickstart_vars()["OCI_IMAGE_REF"], "")

    # ----------------------------------------------------------------- routing
    def test_select_exact_host(self):
        org, status = self.Org._select_for_host("acme.example")
        self.assertEqual(org, self.acme)
        self.assertIsNone(status)

    def test_select_case_and_port(self):
        org, status = self.Org._select_for_host("Acme.Example:443")
        self.assertEqual(org, self.acme)

    def test_select_forwarded_chain(self):
        org, _ = self.Org._select_for_host("acme.example, proxy.internal")
        self.assertEqual(org, self.acme)

    def test_select_unknown_host_refused(self):
        # With several orgs present, an unmatched host must NOT leak another
        # tenant — it is refused (404) rather than falling back.
        org, status = self.Org._select_for_host("nope.example")
        self.assertFalse(org)
        self.assertEqual(status, 404)

    # ----------------------------------------------------------------- render
    def test_render_no_leftover_placeholders(self):
        ks = _render_kickstart(self.acme._kickstart_vars())
        self.assertFalse(_PLACEHOLDER_RE.findall(ks),
                         "kickstart still has unresolved {{PLACEHOLDERS}}")
        self.assertIn("ghcr.io/acme/blue-fox-os-acme:latest", ks)
        self.assertIn("acme.example", ks)
        # The publisher's own domain must not bleed into a tenant's render.
        self.assertNotIn("bluefoxconsultant.com", ks)
