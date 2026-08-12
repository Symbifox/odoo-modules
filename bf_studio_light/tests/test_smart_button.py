"""Tests for the smart button generator (Tier 2.5).

Each test maps to a finding in SECURITY_AUDIT.md or to the Tier 2.5
plan's safety choke points. Controller-level integration is exercised
via the manual smoke test described in the plan; here we verify the
constraint surface and the view-injection back-pointer boundary.
"""

from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase, tagged


@tagged("bf_studio_light", "post_install", "-at_install")
class TestStudioLightSmartButton(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner_model = cls.env["ir.model"]._get("res.partner")
        cls.users_model = cls.env["ir.model"]._get("res.users")
        cls.account_move_model = cls.env["ir.model"]._get("account.move")
        cls.parent_field = cls.env["ir.model.fields"]._get(
            "res.partner", "parent_id"
        )
        cls.partner_id_field = cls.env["ir.model.fields"]._get(
            "res.users", "partner_id"
        )
        # Self-referencing smart button: count children of a partner.
        cls.base_vals = {
            "name": "Children",
            "source_model_id": cls.partner_model.id,
            "target_model_id": cls.partner_model.id,
            "relation_field_id": cls.parent_field.id,
            "label": "Children",
            "icon": "fa-users",
            "color": "text-primary",
        }

    # ------------------------------------------------------------------
    # 1. Lifecycle
    # ------------------------------------------------------------------
    def test_lifecycle_create_button(self):
        """Creating a button materialises an injection + ir.ui.view."""
        sb = self.env["studio.light.smart.button"].create(self.base_vals)
        self.assertTrue(sb.view_injection_id)
        self.assertTrue(sb.view_injection_id.ir_view_id)
        # Re-running provisioning is idempotent.
        existing_view_id = sb.view_injection_id.ir_view_id.id
        self.env["studio.light.smart.button"]._ensure_all_provisioned()
        self.assertEqual(sb.view_injection_id.ir_view_id.id, existing_view_id)

    def test_unlink_cascades_view_and_button(self):
        sb = self.env["studio.light.smart.button"].create(self.base_vals)
        inj = sb.view_injection_id
        view = inj.ir_view_id
        sb.unlink()
        self.assertFalse(inj.exists())
        self.assertFalse(view.exists())

    def test_survival_after_view_deletion(self):
        sb = self.env["studio.light.smart.button"].create(self.base_vals)
        view = sb.view_injection_id.ir_view_id
        view.unlink()
        sb.view_injection_id.invalidate_recordset(["ir_view_id"])
        self.env["studio.light.smart.button"]._ensure_all_provisioned()
        self.assertTrue(
            sb.view_injection_id.ir_view_id
            and sb.view_injection_id.ir_view_id.exists()
        )

    # ------------------------------------------------------------------
    # 2. Locked-model defence (mirrors test_security pattern)
    # ------------------------------------------------------------------
    def test_locked_source_model_refused(self):
        with self.assertRaises(ValidationError):
            self.env["studio.light.smart.button"].create(
                dict(
                    self.base_vals,
                    source_model_id=self.users_model.id,
                    relation_field_id=self.partner_id_field.id,
                    target_model_id=self.users_model.id,
                )
            )

    def test_locked_target_model_refused(self):
        with self.assertRaises(ValidationError):
            self.env["studio.light.smart.button"].create(
                dict(
                    self.base_vals,
                    target_model_id=self.account_move_model.id,
                    relation_field_id=self.env["ir.model.fields"]._get(
                        "account.move", "partner_id"
                    ).id,
                )
            )

    def test_unlocked_group_bypass(self):
        admin = self.env.ref("base.user_admin")
        admin_group = self.env.ref("bf_studio_light.group_studio_light_admin")
        unlocked = self.env.ref(
            "bf_studio_light.group_studio_light_unlocked"
        )
        admin.write({"groups_id": [(4, admin_group.id), (4, unlocked.id)]})
        sb = None
        try:
            sb = self.env["studio.light.smart.button"].with_user(admin).create(
                dict(
                    self.base_vals,
                    name="Logs of user",
                    label="Logs",
                    source_model_id=self.users_model.id,
                    target_model_id=self.users_model.id,
                    relation_field_id=self.env["ir.model.fields"]._get(
                        "res.users", "create_uid"
                    ).id,
                )
            )
            self.assertTrue(sb.view_injection_id)
        finally:
            admin.write({"groups_id": [(3, unlocked.id)]})
            if sb and sb.exists():
                sb.unlink()

    # ------------------------------------------------------------------
    # 3. Relation-field validation
    # ------------------------------------------------------------------
    def test_relation_field_must_be_m2o(self):
        char_field = self.env["ir.model.fields"]._get("res.partner", "name")
        with self.assertRaises(ValidationError):
            self.env["studio.light.smart.button"].create(
                dict(self.base_vals, relation_field_id=char_field.id)
            )

    def test_relation_field_wrong_comodel(self):
        # res.partner.country_id is m2o → res.country, not res.partner.
        country_field = self.env["ir.model.fields"]._get(
            "res.partner", "country_id"
        )
        with self.assertRaises(ValidationError):
            self.env["studio.light.smart.button"].create(
                dict(self.base_vals, relation_field_id=country_field.id)
            )

    def test_relation_field_belongs_to_target(self):
        # If we change target_model_id but keep an old relation field,
        # the constraint must catch the mismatch.
        with self.assertRaises(ValidationError):
            self.env["studio.light.smart.button"].create(
                dict(
                    self.base_vals,
                    # target stays res.partner; relation field is on res.users.
                    relation_field_id=self.partner_id_field.id,
                )
            )

    # ------------------------------------------------------------------
    # 4. Domain validation
    # ------------------------------------------------------------------
    def test_domain_safe_eval_refused(self):
        # __import__ is not a Python literal — literal_eval rejects it.
        with self.assertRaises(ValidationError):
            self.env["studio.light.smart.button"].create(
                dict(
                    self.base_vals,
                    domain="[('name','=', __import__('os').system('id'))]",
                )
            )

    def test_domain_must_be_list(self):
        with self.assertRaises(ValidationError):
            self.env["studio.light.smart.button"].create(
                dict(self.base_vals, domain="{'evil': 'dict'}")
            )

    def test_domain_clauses_must_be_three_tuples(self):
        with self.assertRaises(ValidationError):
            self.env["studio.light.smart.button"].create(
                dict(self.base_vals, domain="[('name', '=')]")
            )

    def test_domain_unknown_operator_refused(self):
        with self.assertRaises(ValidationError):
            self.env["studio.light.smart.button"].create(
                dict(self.base_vals, domain="[('id', 'EXEC', 1)]")
            )

    def test_domain_field_path_invalid_identifier_refused(self):
        with self.assertRaises(ValidationError):
            self.env["studio.light.smart.button"].create(
                dict(self.base_vals, domain="[('name; drop table', '=', 1)]")
            )

    def test_domain_traversing_sensitive_field_refused(self):
        with self.assertRaises(ValidationError):
            self.env["studio.light.smart.button"].create(
                dict(self.base_vals, domain="[('user_ids.password', '=', 1)]")
            )

    def test_domain_valid_passes(self):
        sb = self.env["studio.light.smart.button"].create(
            dict(self.base_vals, domain="[('active', '=', True)]")
        )
        self.assertEqual(sb._parse_domain(), [("active", "=", True)])

    # ------------------------------------------------------------------
    # 5. Label & icon validation
    # ------------------------------------------------------------------
    def test_label_html_refused(self):
        with self.assertRaises(ValidationError):
            self.env["studio.light.smart.button"].create(
                dict(self.base_vals, label="<script>alert(1)</script>")
            )

    def test_label_html_entity_refused(self):
        with self.assertRaises(ValidationError):
            self.env["studio.light.smart.button"].create(
                dict(self.base_vals, label="hello &#60;")
            )

    def test_icon_regex_refused(self):
        with self.assertRaises(ValidationError):
            self.env["studio.light.smart.button"].create(
                dict(
                    self.base_vals,
                    icon="fa-list; background-image:url(//evil)",
                )
            )

    def test_icon_default_accepted(self):
        sb = self.env["studio.light.smart.button"].create(
            dict(self.base_vals, icon="fa-star")
        )
        self.assertEqual(sb.icon, "fa-star")

    # ------------------------------------------------------------------
    # 6. Trusted-arch escape boundary (test #18 in plan)
    # ------------------------------------------------------------------
    def test_arch_trusted_only_for_smart_button_owned_rows(self):
        """A free-form injection must NOT be allowed to inject <widget>
        even if the trusted context flag is forwarded — the back-pointer
        ``studio_smart_button_id`` is required."""
        f = self.env["studio.light.field"].create(
            {
                "label": "test field",
                "name": "x_studio_trust_test",
                "model_id": self.partner_model.id,
                "field_type": "char",
            }
        )
        with self.assertRaises(ValidationError):
            self.env["studio.light.view.injection"].with_context(
                studio_light_trusted_arch=True
            ).create(
                {
                    "name": "trying widget",
                    "studio_field_id": f.id,
                    "model_id": self.partner_model.id,
                    "view_type": "form",
                    "target_field": "email",
                    # No studio_smart_button_id → trusted bit ignored,
                    # whitelist refuses widget.
                    "arch_snippet": '<widget name="evil"/>',
                }
            )

    def test_arch_trusted_requires_context_flag(self):
        """Creating an injection linked to a smart button without the
        trusted context still hits the standard whitelist."""
        sb = self.env["studio.light.smart.button"].create(self.base_vals)
        with self.assertRaises(ValidationError):
            # Without with_context: even with the back-pointer set, the
            # whitelist refuses widget.
            self.env["studio.light.view.injection"].create(
                {
                    "name": "trying widget without context",
                    "model_id": self.partner_model.id,
                    "view_type": "form",
                    "studio_smart_button_id": sb.id,
                    "target_field": "email",
                    "arch_snippet": '<widget name="evil"/>',
                }
            )

    # ------------------------------------------------------------------
    # 7. Action dict construction
    # ------------------------------------------------------------------
    def test_build_action_dict_prepends_relation_filter(self):
        """User domain must be ANDed AFTER the source-id filter so the
        scope can never be widened past records related to source."""
        sb = self.env["studio.light.smart.button"].create(
            dict(self.base_vals, domain="[('active', '=', True)]")
        )
        action = sb._build_action_dict(42)
        self.assertEqual(action["res_model"], "res.partner")
        self.assertEqual(action["domain"][0], ("parent_id", "=", 42))
        self.assertIn(("active", "=", True), action["domain"])

    def test_build_action_dict_no_extra_domain(self):
        sb = self.env["studio.light.smart.button"].create(self.base_vals)
        action = sb._build_action_dict(7)
        self.assertEqual(action["domain"], [("parent_id", "=", 7)])

    # ------------------------------------------------------------------
    # 8. button_box fallback
    # ------------------------------------------------------------------
    def test_button_box_present_no_fallback(self):
        """res.partner has a button_box, so no box-fallback row exists."""
        sb = self.env["studio.light.smart.button"].create(self.base_vals)
        self.assertTrue(sb.view_injection_id)
        self.assertFalse(sb.box_injection_id)
