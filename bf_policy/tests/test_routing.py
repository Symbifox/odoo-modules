"""End-to-end smoke for /api/v1/policy/me.

Verifies the public route exists, resolves a tenant by Host, and refuses an
unauthenticated caller with 401. The org-selection branch logic itself is unit
tested in test_resolve_for_host.TestSelectForHost (no HTTP / no web login, which
is unreliable against a copy of a real database whose admin/oauth auth differs)."""

from odoo.tests import HttpCase, tagged

_URL = "/api/v1/policy/me"


@tagged("post_install", "-at_install")
class TestPolicyRouting(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Org = cls.env["bf.policy.org"]
        # Neutralise any pre-existing org so a.example resolves to ours.
        cls.Org.search([]).write({"active": False})
        cls.company_a = cls.env["res.company"].create({"name": "Tenant A"})
        cls.org_a = cls.Org.create({
            "company_id": cls.company_a.id, "domain": "a.example"})
        cls.env.flush_all()

    def test_resolved_host_unauthenticated_is_401(self):
        # Host matches org_a -> org resolves; no session/bearer -> 401 (not 404).
        resp = self.url_open(_URL, headers={"Host": "a.example"})
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json()["error"], "authentication required")
