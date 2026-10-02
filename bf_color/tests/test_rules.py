import ast

from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, new_test_user, tagged

from ..models.color_utils import DEFAULT_PALETTE


@tagged("post_install", "-at_install")
class TestRules(TransactionCase):
    """Rules, following the clinic example: visits colored by their doctor."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.alice = new_test_user(cls.env, "bfr_alice", groups="base.group_user")
        Category = cls.env["res.partner.category"]
        cls.dr_a = Category.create({"name": "Dr A", "color": 0, "color_hex": "#FF0000"})
        cls.dr_b = Category.create({"name": "Dr B", "color": 0, "color_hex": "#0000FF"})
        cls.dr_c = Category.create({"name": "Dr C", "color": 0})
        cls.visit_a = Category.create({"name": "Visit A", "parent_id": cls.dr_a.id, "color": 0})
        cls.visit_b = Category.create({"name": "Visit B", "parent_id": cls.dr_b.id, "color": 0})
        cls.visit_c = Category.create({"name": "Visit C", "parent_id": cls.dr_c.id, "color": 0})
        cls.model = cls.env["ir.model"]._get("res.partner.category")

    def _rule(self, field="parent_id", **vals):
        return self.env["bf.color.rule"].create(dict({
            "name": "Visits by doctor",
            "model_id": self.model.id,
            "field_id": self.env["ir.model.fields"]._get("res.partner.category", field).id,
        }, **vals))

    def _swatch(self, *colors):
        return self.env["bf.color.swatch"].create({
            "name": "Clinic",
            "line_ids": [(0, 0, {"color_hex": color, "sequence": i}) for i, color in enumerate(colors)],
        })

    def _seen_by(self, user, records):
        return records.with_user(user).with_company(user.company_id)

    def test_value_color_is_followed(self):
        """Dr A's color, set once on Dr A, colors Dr A's visits."""
        self._rule()
        visits = self._seen_by(self.alice, self.visit_a | self.visit_b | self.visit_c)
        self.assertEqual(visits[0].color_resolved, "#FF0000")
        self.assertEqual(visits[0].color_source, "rule")
        self.assertEqual(visits[1].color_resolved, "#0000FF")
        # Dr C has no color and the rule no swatch: the visit keeps no color.
        self.assertFalse(visits[2].color_resolved)

    def test_users_own_color_for_the_doctor_follows(self):
        """Alice's own color for Dr A shows on Dr A's visits, for Alice only."""
        self._rule()
        self._seen_by(self.alice, self.dr_a).bf_color_set_mine("#00AA00")
        self.assertEqual(self._seen_by(self.alice, self.visit_a).color_resolved, "#00AA00")
        self.assertEqual(self.visit_a.with_company(self.env.company).color_resolved, "#FF0000")

    def test_line_beats_value_color(self):
        self._rule(line_ids=[(0, 0, {"value_res_id": self.dr_a.id, "color_hex": "#123456"})])
        self.assertEqual(self.visit_a.color_resolved, "#123456")
        self.assertEqual(self.visit_b.color_resolved, "#0000FF")

    def test_only_listed_values(self):
        self._rule(only_listed=True, line_ids=[(0, 0, {"value_res_id": self.dr_a.id, "color_hex": "#123456"})])
        self.assertEqual(self.visit_a.color_resolved, "#123456")
        # Dr B is not listed: Dr B's color is not followed, the visit keeps its own (none).
        self.assertFalse(self.visit_b.color_resolved)

    def test_only_listed_values_ignore_the_fallback_swatch(self):
        """A swatch left on a rule switched to "listed values only" colors nothing else."""
        self._rule(only_listed=True, fallback_swatch_id=self._swatch("#445566").id,
                   line_ids=[(0, 0, {"value_res_id": self.dr_a.id, "color_hex": "#123456"})])
        self.assertEqual(self.visit_a.color_resolved, "#123456")
        self.assertFalse(self.visit_c.color_resolved)

    def test_fallback_swatch_for_values_without_color(self):
        self._rule(fallback_swatch_id=self._swatch("#445566", "#778899").id)
        self.assertIn(self.visit_c.color_resolved, ("#445566", "#778899"))
        self.assertEqual(self.visit_a.color_resolved, "#FF0000")

    def test_many2many_line_order_is_the_priority(self):
        Partner = self.env["res.partner"]
        first, second = Partner.create({"name": "P1"}), Partner.create({"name": "P2"})
        tag = self.env["res.partner.category"].create({
            "name": "Two values", "color": 0, "partner_ids": [(6, 0, (first | second).ids)],
        })
        rule = self._rule(field="partner_ids", line_ids=[
            (0, 0, {"value_res_id": second.id, "color_hex": "#0000AA", "sequence": 1}),
            (0, 0, {"value_res_id": first.id, "color_hex": "#AA0000", "sequence": 2}),
        ])
        self.assertEqual(tag.color_resolved, "#0000AA")
        rule.line_ids.filtered(lambda line: line.value_res_id == first.id).sequence = 0
        self.env.invalidate_all()
        self.assertEqual(tag.color_resolved, "#AA0000")

    def test_rule_needs_a_colorable_model(self):
        with self.assertRaises(ValidationError):
            self.env["bf.color.rule"].create({
                "name": "Contacts",
                "model_id": self.env["ir.model"]._get("res.partner").id,
                "field_id": self.env["ir.model.fields"]._get("res.partner", "user_id").id,
            })
        rule = self._rule()
        self.assertTrue(rule.value_colorable)

    def test_new_rule_form_offers_the_wired_models(self):
        """The model field of a rule searches on the server: wired models only."""
        domain = self.env["bf.color.rule"]._fields["model_id"].domain
        Model = self.env["ir.model"]  # rules are managed by administrators
        found = Model.name_search("", args=ast.literal_eval(domain), limit=None)
        names = set(Model.browse([row[0] for row in found]).mapped("model"))
        self.assertIn("res.partner.category", names)
        self.assertNotIn("res.partner", names)
        self.assertTrue(self.model.bf_color_colorable)
        self.assertFalse(self.env["ir.model"]._get("res.partner").bf_color_colorable)

    def test_assign_missing_writes_least_used(self):
        rule = self._rule(fallback_swatch_id=self._swatch("#FF0000", "#00FF00", "#0000FF").id)
        rule.action_assign_missing()
        # Red and blue are taken by Dr A and Dr B: Dr C gets green, on the record.
        self.assertEqual(self.dr_c.color_hex, "#00FF00")
        self.assertEqual(self.visit_c.color_resolved, "#00FF00")
        self.assertEqual(self.dr_a.color_hex, "#FF0000")

    def test_assign_missing_without_swatch_uses_default_palette(self):
        rule = self._rule()
        rule.action_assign_missing()
        self.assertIn(self.dr_c.color_hex, DEFAULT_PALETTE)

    def test_new_value_gets_a_color_once(self):
        self._rule(fallback_swatch_id=self._swatch("#FF0000", "#00FF00", "#0000FF").id)
        dr_d = self.env["res.partner.category"].create({"name": "Dr D", "color": 0})
        self.assertEqual(dr_d.color_hex, "#00FF00")
        dr_e = self.env["res.partner.category"].create({"name": "Dr E", "color": 0})
        self.assertNotEqual(dr_e.color_hex, dr_d.color_hex)
        # A value created with its own color keeps it.
        dr_f = self.env["res.partner.category"].create({"name": "Dr F", "color_hex": "#ABCDEF"})
        self.assertEqual(dr_f.color_hex, "#ABCDEF")

    def test_no_new_color_without_swatch(self):
        self._rule()
        dr_d = self.env["res.partner.category"].create({"name": "Dr D", "color": 0})
        self.assertFalse(dr_d.color_hex)

    def test_value_color_depth_is_bounded(self):
        """A chain of values each following the next stops after a few hops."""
        self._rule()
        Category = self.env["res.partner.category"]
        top = Category.create({"name": "Top", "color": 0, "color_hex": "#ABCDEF"})
        chain = [top]
        for i in range(6):
            chain.append(Category.create({"name": "Level %s" % i, "color": 0, "parent_id": chain[-1].id}))
        self.env.invalidate_all()
        self.assertEqual(chain[1].color_resolved, "#ABCDEF")
        # Past the first value, only a value's OWN color is followed: Level 0
        # has none, so Level 1 gets nothing through it.
        self.assertFalse(chain[2].color_resolved)
        self.assertFalse(chain[-1].color_resolved)

    def test_assign_missing_is_reserved_to_rule_managers(self):
        """Public method, writes in sudo: a reader of the rule may not run it."""
        rule = self._rule(fallback_swatch_id=self._swatch("#00FF00").id)
        # Warm the cache with what the method reads (the criterion field): a
        # caller who may read ir.model.fields gets that far, and only the guard
        # stands between them and a write in sudo.
        rule.field_id.name, rule.model_name, rule.value_colorable  # noqa: B018
        # Not under assertRaises: Odoo's version wraps the block in a savepoint
        # and flushes on exit, and an error raised by that flush satisfied it
        # while the method itself had run and written.
        try:
            rule.with_user(self.alice).action_assign_missing()
        except AccessError:
            pass
        else:
            self.fail("A mere reader of the rule assigned colors in sudo.")
        self.assertFalse(self.dr_c.color_hex)

    def test_free_color_is_always_a_clean_hex(self):
        """It ends up in a style attribute: anything but #RRGGBB is refused."""
        with self.assertRaises(ValidationError):
            self.dr_c.write({"color": 1, "color_hex": "red;position:fixed;inset:0"})
        with self.assertRaises(ValidationError):
            self.env["res.partner.category"].create({"name": "X", "color_hex": "url(https://x/y)"})
        self.dr_c.write({"color_hex": "#abc"})
        self.assertEqual(self.dr_c.color_hex, "#AABBCC")

    def test_archived_rule_colors_nothing(self):
        rule = self._rule()
        rule.active = False
        self.env.invalidate_all()
        self.assertFalse(self.visit_a.with_context(active_test=False).color_resolved)
