from odoo.exceptions import AccessError
from odoo.models import check_method_name
from odoo.tests import TransactionCase, tagged


@tagged("bf_universal_search", "universal_search")
class TestUniversalSearch(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Search = cls.env["bf.universal.search"]
        cls.Config = cls.env["bf.universal.search.config"]
        cls.Model = cls.env["ir.model"]

        # Clean slate for deterministic results
        cls.Config.search([]).write({"active": False})

        cls.partner_model = cls.Model._get("res.partner")
        cls.config = cls.Config.create({
            "name": "Contacts",
            "model_id": cls.partner_model.id,
            "search_fields": "name,email",
            "category": "search_contacts",
            "icon": "fa fa-users",
            "limit": 5,
        })
        # Rename an existing partner instead of creating one: in -i mode,
        # bf_universal_search loads before the account module, so res.partner's
        # Python definition lacks account's autopost_bills field and create()
        # fails while write() on existing rows is unaffected.
        cls.partner = cls.env.ref("base.partner_admin")
        cls.partner.write({
            "name": "ZZZ Universal Search Target",
            "email": "usearch@example.com",
        })

    # --- Config model ---

    def test_config_related_model_name(self):
        self.assertEqual(self.config.model_name, "res.partner")

    def test_config_defaults(self):
        c = self.Config.create({
            "name": "Test",
            "model_id": self.partner_model.id,
            "search_fields": "name",
            "category": "t",
        })
        self.assertEqual(c.icon, "fa fa-search")
        self.assertEqual(c.sequence, 100)
        self.assertEqual(c.limit, 5)
        self.assertTrue(c.active)

    # --- search_all ---

    def test_search_all_too_short_query_returns_empty(self):
        self.assertEqual(self.Search.search_all("z"), [])
        self.assertEqual(self.Search.search_all(""), [])

    def test_search_all_finds_matching_partner(self):
        groups = self.Search.search_all("ZZZ Universal")
        # Should have at least the contacts group with our seeded partner
        matching_groups = [g for g in groups if g["model"] == "res.partner"]
        self.assertTrue(matching_groups)
        result_ids = [r["id"] for r in matching_groups[0]["results"]]
        self.assertIn(self.partner.id, result_ids)

    def test_search_all_respects_model_filters(self):
        groups = self.Search.search_all(
            "ZZZ Universal", model_filters=["res.users"]
        )
        # res.partner config should be filtered out
        self.assertEqual(
            [g for g in groups if g["model"] == "res.partner"], []
        )

    def test_search_all_skips_inactive_configs(self):
        self.config.active = False
        groups = self.Search.search_all("ZZZ Universal")
        self.assertEqual(
            [g for g in groups if g["model"] == "res.partner"], []
        )

    def test_search_all_returns_expected_shape(self):
        groups = self.Search.search_all("ZZZ Universal")
        matching = [g for g in groups if g["model"] == "res.partner"]
        self.assertTrue(matching)
        g = matching[0]
        self.assertIn("model_label", g)
        self.assertIn("icon", g)
        self.assertIn("category", g)
        self.assertIn("results", g)
        if g["results"]:
            r = g["results"][0]
            self.assertIn("id", r)
            self.assertIn("name", r)
            self.assertIn("detail", r)
            self.assertIn("closed", r)

    # --- Search by record id (18.0.2.0.0) ---

    def test_numeric_query_matches_record_id(self):
        self.config.search_by_id = True
        # A bare number must be at least 2 characters (see the floor test below),
        # so target a record whose id has 2+ digits.
        target = self.env["res.partner"].search([("id", ">=", 10)], limit=1)
        if not target:
            self.skipTest("no partner with a 2-digit id in this database")
        target.write({"name": "ZZZ Universal Search Numeric"})
        groups = self.Search.search_all(str(target.id))
        result_ids = [
            r["id"]
            for g in groups if g["model"] == "res.partner"
            for r in g["results"]
        ]
        self.assertIn(target.id, result_ids)

    def test_single_character_query_is_below_the_floor(self):
        # The 2-character floor keeps the palette from querying every model on the
        # first keystroke. A 1-digit id stays reachable through the "#" prefix,
        # which is what test_numeric_query_accepts_hash_prefix covers.
        self.config.search_by_id = True
        if self.partner.id >= 10:
            self.skipTest("this partner's id is not a single digit")
        self.assertEqual(self.Search.search_all(str(self.partner.id)), [])

    def test_numeric_query_accepts_hash_prefix(self):
        self.config.search_by_id = True
        groups = self.Search.search_all("#%d" % self.partner.id)
        result_ids = [
            r["id"]
            for g in groups if g["model"] == "res.partner"
            for r in g["results"]
        ]
        self.assertIn(self.partner.id, result_ids)

    def test_numeric_query_ignored_when_search_by_id_is_off(self):
        self.config.search_by_id = False
        groups = self.Search.search_all(str(self.partner.id))
        result_ids = [
            r["id"]
            for g in groups if g["model"] == "res.partner"
            for r in g["results"]
        ]
        self.assertNotIn(self.partner.id, result_ids)

    # --- Closed records: sorted last and flagged (18.0.2.0.0) ---

    def test_closed_records_are_flagged_and_sorted_last(self):
        open_partner = self.partner
        closed_partner = self.env["res.partner"].search(
            [("id", "!=", open_partner.id)], limit=1
        )
        closed_partner.write({
            "name": "ZZZ Universal Search Closed",
            "comment": "closed-marker",
        })
        self.config.closed_domain = "[('comment', 'ilike', 'closed-marker')]"

        groups = self.Search.search_all("ZZZ Universal Search")
        results = next(
            g["results"] for g in groups if g["model"] == "res.partner"
        )
        by_id = {r["id"]: r for r in results}
        self.assertIn(open_partner.id, by_id)
        self.assertIn(closed_partner.id, by_id)
        self.assertFalse(by_id[open_partner.id]["closed"])
        self.assertTrue(by_id[closed_partner.id]["closed"])
        # the closed one must come after the open one
        ids_in_order = [r["id"] for r in results]
        self.assertLess(
            ids_in_order.index(open_partner.id),
            ids_in_order.index(closed_partner.id),
        )

    # --- Domain, min_length, detail_fields (18.0.2.0.0) ---

    def test_domain_filters_out_records(self):
        self.config.domain = "[('id', '=', 0)]"
        groups = self.Search.search_all("ZZZ Universal")
        self.assertEqual(
            [g for g in groups if g["model"] == "res.partner"], []
        )

    def test_domain_accepts_relative_dates(self):
        # An ir.filters-style domain must evaluate without blowing up.
        self.config.domain = (
            "[('create_date', '<=', (context_today() + "
            "dateutil.relativedelta.relativedelta(days=1))"
            ".strftime('%Y-%m-%d'))]"
        )
        groups = self.Search.search_all("ZZZ Universal")
        self.assertTrue([g for g in groups if g["model"] == "res.partner"])

    def test_invalid_domain_is_rejected_at_write(self):
        from odoo.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            self.config.domain = "not a domain"

    def test_min_length_skips_short_queries(self):
        self.config.min_length = 8
        self.assertEqual(
            [g for g in self.Search.search_all("ZZZ") if g["model"] == "res.partner"],
            [],
        )
        self.assertTrue([
            g for g in self.Search.search_all("ZZZ Universal")
            if g["model"] == "res.partner"
        ])

    def test_detail_fields_build_the_context_line(self):
        self.config.detail_fields = "email"
        groups = self.Search.search_all("ZZZ Universal")
        result = next(
            r
            for g in groups if g["model"] == "res.partner"
            for r in g["results"] if r["id"] == self.partner.id
        )
        self.assertEqual(result["detail"], "usearch@example.com")

    # --- The domain evaluator must never be reachable over RPC (18.0.2.0.1) ---

    def test_eval_domain_is_private_and_rpc_blocked(self):
        # A public (RPC-callable) name here would let any user run safe_eval on
        # an arbitrary string. The method must stay underscore-prefixed so Odoo's
        # call_kw gate rejects it.
        self.assertFalse(hasattr(self.Config, "eval_domain"))
        self.assertTrue(hasattr(self.Config, "_eval_domain"))
        with self.assertRaises(AccessError):
            check_method_name("_eval_domain")

    def test_limit_is_capped(self):
        # A caller-supplied limit must not turn search_all into a bulk dump.
        self.config.limit = 0  # fall back to the RPC-supplied limit
        groups = self.Search.search_all("ZZZ Universal", limit=10_000)
        results = next(
            g["results"] for g in groups if g["model"] == "res.partner"
        )
        self.assertLessEqual(len(results), 20)

    def test_non_string_model_filters_do_not_crash(self):
        # A malformed model_filters must be ignored, not raise a 500.
        self.assertEqual(self.Search.search_all("ZZZ Universal", model_filters=5), [])
