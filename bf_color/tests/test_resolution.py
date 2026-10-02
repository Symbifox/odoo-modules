from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestResolution(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.alice = new_test_user(cls.env, "bfc_alice", groups="base.group_user")
        cls.bob = new_test_user(cls.env, "bfc_bob", groups="base.group_user")
        cls.admin = new_test_user(cls.env, "bfc_admin", groups="base.group_user,base.group_system")
        Category = cls.env["res.partner.category"]
        cls.tag = Category.create({"name": "BFC tag", "color": 1})
        # Odoo gives a new tag a random color: the doctors start without one.
        cls.dr_a = Category.create({"name": "Dr A", "color": 0})
        cls.dr_b = Category.create({"name": "Dr B", "color": 0})
        cls.dr_c = Category.create({"name": "Dr C", "color": 0})
        cls.visit_a = Category.create({"name": "Visit A", "parent_id": cls.dr_a.id})
        cls.visit_b = Category.create({"name": "Visit B", "parent_id": cls.dr_b.id})
        cls.visit_c = Category.create({"name": "Visit C", "parent_id": cls.dr_c.id})

    def _as(self, user, record):
        return record.with_user(user).with_company(user.company_id)

    def test_record_index_is_the_default(self):
        tag = self._as(self.alice, self.tag)
        self.assertEqual(tag.color_resolved, "#EE2D2D")
        self.assertEqual(tag.color_resolved_index, 1)
        self.assertEqual(tag.color_source, "record")

    def test_own_hex_beats_index(self):
        self.tag.color_hex = "#123456"
        tag = self._as(self.alice, self.tag)
        self.assertEqual(tag.color_resolved, "#123456")
        self.assertEqual(tag.color_text, "#FFFFFF")

    def test_user_override_is_personal(self):
        self._as(self.alice, self.tag).bf_color_set_mine("#00ff00")
        self.assertEqual(self._as(self.alice, self.tag).color_resolved, "#00FF00")
        self.assertEqual(self._as(self.alice, self.tag).color_source, "user")
        self.assertEqual(self._as(self.bob, self.tag).color_resolved, "#EE2D2D")

    def test_order_user_company_rule_record(self):
        self._as(self.admin, self.tag).bf_color_set_company("#0000FF")
        self.assertEqual(self._as(self.bob, self.tag).color_resolved, "#0000FF")
        self.assertEqual(self._as(self.bob, self.tag).color_source, "company")
        self._as(self.bob, self.tag).bf_color_set_mine("#FF00FF")
        self.assertEqual(self._as(self.bob, self.tag).color_resolved, "#FF00FF")
        self._as(self.bob, self.tag).bf_color_clear_mine()
        self.assertEqual(self._as(self.bob, self.tag).color_resolved, "#0000FF")

    def test_company_color_needs_admin(self):
        with self.assertRaises(AccessError):
            self._as(self.alice, self.tag).bf_color_set_company("#0000FF")

    def test_bad_hex_refused(self):
        with self.assertRaises(ValidationError):
            self._as(self.alice, self.tag).bf_color_set_mine("rouge")

    def test_override_rows_are_private(self):
        self._as(self.alice, self.tag).bf_color_set_mine("#00FF00")
        rows = self.env["bf.color.override"].with_user(self.bob).search([])
        self.assertFalse(rows.filtered(lambda r: r.user_id == self.alice))

    def test_clinic_rule_with_fallback(self):
        swatch = self.env["bf.color.swatch"].create({
            "name": "Fallback",
            "line_ids": [(0, 0, {"color_hex": "#445566"}), (0, 0, {"color_hex": "#778899"})],
        })
        model = self.env["ir.model"]._get("res.partner.category")
        field = self.env["ir.model.fields"]._get("res.partner.category", "parent_id")
        self.env["bf.color.rule"].create({
            "name": "Clinic",
            "model_id": model.id,
            "field_id": field.id,
            "fallback_swatch_id": swatch.id,
            "line_ids": [
                (0, 0, {"value_res_id": self.dr_a.id, "color_hex": "#ff0000"}),
                (0, 0, {"value_res_id": self.dr_b.id, "color_hex": "#0000ff"}),
            ],
        })
        visits = self._as(self.alice, self.visit_a | self.visit_b | self.visit_c)
        self.assertEqual(visits[0].color_resolved, "#FF0000")
        self.assertEqual(visits[0].color_source, "rule")
        self.assertEqual(visits[1].color_resolved, "#0000FF")
        self.assertIn(visits[2].color_resolved, ("#445566", "#778899"))
        again = self._as(self.bob, self.visit_c).color_resolved
        self.assertEqual(visits[2].color_resolved, again)
        # A doctor with no parent is not colored by the rule.
        self.assertNotEqual(self._as(self.alice, self.dr_a).color_source, "rule")
        # The user's own choice still wins over the rule.
        self._as(self.alice, self.visit_a).bf_color_set_mine("#010203")
        self.assertEqual(self._as(self.alice, self.visit_a).color_resolved, "#010203")

    def test_rule_field_must_belong_to_model(self):
        model = self.env["ir.model"]._get("res.partner.category")
        field = self.env["ir.model.fields"]._get("res.partner", "name")
        with self.assertRaises(ValidationError):
            self.env["bf.color.rule"].create(
                {"name": "Bad", "model_id": model.id, "field_id": field.id}
            )

    def test_unlink_cleans_overrides(self):
        self._as(self.alice, self.tag).bf_color_set_mine("#00FF00")
        tag_id = self.tag.id
        self.tag.unlink()
        self.assertFalse(self.env["bf.color.override"].search(
            [("res_model", "=", "res.partner.category"), ("res_id", "=", tag_id)]
        ))

    def test_own_hex_keeps_odoo_index_close(self):
        self.tag.color_hex = "#ff1010"
        self.assertEqual(self.tag.color, 1)
        self.tag.color_hex = "#50c060"
        self.assertEqual(self.tag.color, 10)
        self.tag.color_hex = False
        self.assertEqual(self.tag.color, 0)
        created = self.env["res.partner.category"].create({"name": "BFC new", "color_hex": "#abc"})
        self.assertEqual(created.color_hex, "#AABBCC")

    def test_odoo_index_alone_clears_own_hex(self):
        self.tag.color_hex = "#123456"
        self.tag.color = 4
        self.assertFalse(self.tag.color_hex)
        self.assertEqual(self._as(self.alice, self.tag).color_resolved, "#5794DD")

    def test_override_owner_cannot_be_changed_after_the_fact(self):
        """Rules run before a write only: a user's own override must not become a
        company color, nor someone else's."""
        self._as(self.alice, self.tag).bf_color_set_mine("#00FF00")
        override = self.env["bf.color.override"].search([("user_id", "=", self.alice.id)])
        mine = override.with_user(self.alice)
        with self.assertRaises(AccessError):
            mine.write({"user_id": False, "company_id": self.alice.company_id.id})
        with self.assertRaises(AccessError):
            mine.write({"user_id": self.bob.id})
        mine.write({"color_hex": "#123456"})
        self.assertEqual(override.color_hex, "#123456")
