"""Unit tests for bf.policy.org._resolve_for_host (host routing)."""

from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestResolveForHost(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Org = cls.env["bf.policy.org"]
        # Two companies so two org rows can coexist (company_uniq).
        cls.company_a = cls.env["res.company"].create({"name": "Tenant A"})
        cls.company_b = cls.env["res.company"].create({"name": "Tenant B"})
        cls.org_a = cls.Org.create({
            "company_id": cls.company_a.id, "domain": "a.example",
        })
        cls.org_b = cls.Org.create({
            "company_id": cls.company_b.id, "domain": "b.example",
        })

    def test_resolve_exact(self):
        self.assertEqual(self.Org._resolve_for_host("a.example"), self.org_a)
        self.assertEqual(self.Org._resolve_for_host("b.example"), self.org_b)

    def test_resolve_case_and_port(self):
        self.assertEqual(self.Org._resolve_for_host("A.Example:443"), self.org_a)

    def test_resolve_forwarded_chain(self):
        # X-Forwarded-Host can be a comma chain; the first hop wins.
        self.assertEqual(
            self.Org._resolve_for_host("b.example, proxy.internal"), self.org_b)

    def test_resolve_no_match_is_empty(self):
        self.assertFalse(self.Org._resolve_for_host("unknown.example"))
        self.assertFalse(self.Org._resolve_for_host(""))
        self.assertFalse(self.Org._resolve_for_host(None))

    def test_resolve_ignores_archived(self):
        self.org_a.active = False
        self.assertFalse(self.Org._resolve_for_host("a.example"))


@tagged("post_install", "-at_install")
class TestSelectForHost(TransactionCase):
    """The controller's org-selection branch logic (host match, single-org
    fallback, multi-org 404, no-org 503), tested without HTTP."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Org = cls.env["bf.policy.org"]
        # The DB may hold real rows — archive pre-existing rows so the active
        # count is exactly what each test creates. Rolled back at teardown.
        cls.Org.search([]).write({"active": False})
        cls.company_a = cls.env["res.company"].create({"name": "Tenant A"})
        cls.company_b = cls.env["res.company"].create({"name": "Tenant B"})

    def test_matched_host_wins(self):
        org_a = self.Org.create({"company_id": self.company_a.id, "domain": "a.example"})
        self.Org.create({"company_id": self.company_b.id, "domain": "b.example"})
        org, status = self.Org._select_for_host("a.example")
        self.assertEqual(org, org_a)
        self.assertIsNone(status)

    def test_single_org_fallback(self):
        org_a = self.Org.create({"company_id": self.company_a.id, "domain": "a.example"})
        org, status = self.Org._select_for_host("nope.example")
        self.assertEqual(org, org_a)
        self.assertIsNone(status)

    def test_multi_org_unmatched_is_404(self):
        self.Org.create({"company_id": self.company_a.id, "domain": "a.example"})
        self.Org.create({"company_id": self.company_b.id, "domain": "b.example"})
        org, status = self.Org._select_for_host("nope.example")
        self.assertFalse(org)
        self.assertEqual(status, 404)

    def test_no_org_is_503(self):
        org, status = self.Org._select_for_host("a.example")
        self.assertFalse(org)
        self.assertEqual(status, 503)
