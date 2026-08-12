"""Security regression tests for bf_studio_light.

Each test corresponds to a finding in SECURITY_AUDIT.md.
"""

from odoo.exceptions import AccessError, ValidationError
from odoo.tests.common import TransactionCase, tagged


@tagged("bf_studio_light", "post_install", "-at_install")
class TestStudioLightSecurity(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner_model = cls.env["ir.model"]._get("res.partner")
        cls.users_model = cls.env["ir.model"]._get("res.users")

    # S5 — Locked models refused
    def test_locked_model_refused_without_group(self):
        """res.users is locked; create must fail without bypass group."""
        with self.assertRaises(ValidationError):
            self.env["studio.light.field"].create(
                {
                    "label": "Trying users",
                    "name": "x_studio_pwn",
                    "model_id": self.users_model.id,
                    "field_type": "char",
                }
            )

    # S5 — Relational target on locked model refused
    def test_relational_target_locked_model_refused(self):
        """A many2many pointing at res.users must be refused without the
        bypass group, even when the host model (res.partner) is open."""
        with self.assertRaises(ValidationError):
            self.env["studio.light.field"].create(
                {
                    "label": "Linked users m2m",
                    "name": "x_studio_user_m2m",
                    "model_id": self.partner_model.id,
                    "field_type": "many2many",
                    "relation_model_id": self.users_model.id,
                }
            )

    # S5 — Reference whitelist containing a locked model refused
    def test_reference_whitelist_locked_model_refused(self):
        """A reference field that lists res.users among its allowed
        targets must be refused without the bypass group."""
        with self.assertRaises(ValidationError):
            self.env["studio.light.field"].create(
                {
                    "label": "Polymorphic with users",
                    "name": "x_studio_ref_poison",
                    "model_id": self.partner_model.id,
                    "field_type": "reference",
                    "reference_model_ids": [
                        (6, 0, [self.partner_model.id, self.users_model.id])
                    ],
                }
            )

    # S6 — Context-only bypass no longer works
    def test_context_force_does_not_bypass(self):
        """Setting studio_light_force in context must NOT bypass the lock."""
        with self.assertRaises(ValidationError):
            self.env["studio.light.field"].with_context(
                studio_light_force=True
            ).create(
                {
                    "label": "Trying users via context",
                    "name": "x_studio_pwn2",
                    "model_id": self.users_model.id,
                    "field_type": "char",
                }
            )

    # S6 — Group-based bypass works
    def test_unlocked_group_bypass(self):
        """Members of both admin and unlocked groups can target locked models."""
        admin = self.env.ref("base.user_admin")
        admin_group = self.env.ref("bf_studio_light.group_studio_light_admin")
        unlocked = self.env.ref("bf_studio_light.group_studio_light_unlocked")
        admin.write(
            {"groups_id": [(4, admin_group.id), (4, unlocked.id)]}
        )
        f = None
        try:
            f = self.env["studio.light.field"].with_user(admin).create(
                {
                    "label": "Allowed because unlocked",
                    "name": "x_studio_legit_users",
                    "model_id": self.users_model.id,
                    "field_type": "char",
                }
            )
            self.assertTrue(f.ir_model_field_id)
        finally:
            admin.write({"groups_id": [(3, unlocked.id)]})
            if f and f.exists():
                f.unlink()

    # S3 — Sensitive field denylist
    def test_related_path_to_password_refused(self):
        """Related path traversing 'password' must be refused."""
        with self.assertRaises(ValidationError):
            self.env["studio.light.field"].create(
                {
                    "label": "Leak password",
                    "name": "x_studio_leak",
                    "model_id": self.partner_model.id,
                    "field_type": "char",
                    "is_related": True,
                    "related_path": "user_ids.password",
                }
            )

    # S3 — Locked-model traversal
    def test_related_path_through_locked_model_refused(self):
        """Related path stepping into res.users must be refused."""
        with self.assertRaises(ValidationError):
            self.env["studio.light.field"].create(
                {
                    "label": "Leak user login",
                    "name": "x_studio_leak2",
                    "model_id": self.partner_model.id,
                    "field_type": "char",
                    "is_related": True,
                    "related_path": "user_ids.login",
                }
            )

    # S1 — Arch snippet button injection
    def test_arch_snippet_button_refused(self):
        """<button> in arch_snippet must be refused."""
        f = self.env["studio.light.field"].create(
            {
                "label": "Test field",
                "name": "x_studio_test_btn",
                "model_id": self.partner_model.id,
                "field_type": "char",
            }
        )
        with self.assertRaises(ValidationError):
            self.env["studio.light.view.injection"].create(
                {
                    "name": "Evil button injection",
                    "studio_field_id": f.id,
                    "model_id": self.partner_model.id,
                    "view_type": "form",
                    "target_field": "email",
                    "arch_snippet": '<button name="action_unlink" type="object" string="oops"/>',
                }
            )

    # S1 — Forbidden tag (header)
    def test_arch_snippet_header_refused(self):
        f = self.env["studio.light.field"].create(
            {
                "label": "Test field",
                "name": "x_studio_test_hdr",
                "model_id": self.partner_model.id,
                "field_type": "char",
            }
        )
        with self.assertRaises(ValidationError):
            self.env["studio.light.view.injection"].create(
                {
                    "name": "Header injection",
                    "studio_field_id": f.id,
                    "model_id": self.partner_model.id,
                    "view_type": "form",
                    "target_field": "email",
                    "arch_snippet": "<header><button/></header>",
                }
            )

    # S1 — XPath logical operators refused
    def test_custom_xpath_or_refused(self):
        f = self.env["studio.light.field"].create(
            {
                "label": "Test xpath",
                "name": "x_studio_test_xp",
                "model_id": self.partner_model.id,
                "field_type": "char",
            }
        )
        with self.assertRaises(ValidationError):
            self.env["studio.light.view.injection"].create(
                {
                    "name": "Broad xpath",
                    "studio_field_id": f.id,
                    "model_id": self.partner_model.id,
                    "view_type": "form",
                    "custom_xpath": "//field[@name='email'] or //field[@name='phone']",
                }
            )

    # S1 — XPath wildcard predicate refused
    def test_custom_xpath_wildcard_refused(self):
        f = self.env["studio.light.field"].create(
            {
                "label": "Test wild",
                "name": "x_studio_test_wild",
                "model_id": self.partner_model.id,
                "field_type": "char",
            }
        )
        with self.assertRaises(ValidationError):
            self.env["studio.light.view.injection"].create(
                {
                    "name": "Wild xpath",
                    "studio_field_id": f.id,
                    "model_id": self.partner_model.id,
                    "view_type": "form",
                    "custom_xpath": "//*[@name='email']",
                }
            )

    # S1 — target_field with quote refused
    def test_target_field_quote_refused(self):
        f = self.env["studio.light.field"].create(
            {
                "label": "Test quote",
                "name": "x_studio_test_q",
                "model_id": self.partner_model.id,
                "field_type": "char",
            }
        )
        with self.assertRaises(ValidationError):
            self.env["studio.light.view.injection"].create(
                {
                    "name": "Quote injection",
                    "studio_field_id": f.id,
                    "model_id": self.partner_model.id,
                    "view_type": "form",
                    "target_field": "x'/><evil",
                }
            )

    # TA-S3 — Modifier expression rejects function calls
    def test_modifier_expression_rejects_function_call(self):
        """Conditional modifier expressions must refuse any function call
        — that's the only sandbox we have between user input and
        Odoo's view-engine `safe_eval`."""
        with self.assertRaises(ValidationError):
            self.env["studio.light.field"].create(
                {
                    "label": "Evil modifier call",
                    "name": "x_studio_evil_call",
                    "model_id": self.partner_model.id,
                    "field_type": "char",
                    "invisible_expr": "__import__('os').system('echo pwned')",
                }
            )

    def test_modifier_expression_rejects_subscript(self):
        """`x[y]` access is rejected — keeps the eval surface narrow."""
        with self.assertRaises(ValidationError):
            self.env["studio.light.field"].create(
                {
                    "label": "Subscript abuse",
                    "name": "x_studio_subscript_evil",
                    "model_id": self.partner_model.id,
                    "field_type": "char",
                    "readonly_expr": "vars()['__builtins__']",
                }
            )

    def test_modifier_expression_rejects_comprehension(self):
        with self.assertRaises(ValidationError):
            self.env["studio.light.field"].create(
                {
                    "label": "Comprehension abuse",
                    "name": "x_studio_comp_evil",
                    "model_id": self.partner_model.id,
                    "field_type": "char",
                    "required_expr": "[x for x in self.env]",
                }
            )

    def test_modifier_expression_accepts_idiomatic(self):
        """Sanity check the validator isn't over-zealous — the standard
        Odoo idiom must pass."""
        f = self.env["studio.light.field"].create(
            {
                "label": "Idiom modifier",
                "name": "x_studio_idiom_mod",
                "model_id": self.partner_model.id,
                "field_type": "char",
                "invisible_expr": "is_company and state == 'draft'",
                "required_expr": "category_id and not is_company",
            }
        )
        self.assertTrue(f.id)
        f.unlink()

    # S12 — replace position not selectable
    def test_position_replace_not_available(self):
        f = self.env["studio.light.field"].create(
            {
                "label": "Test pos",
                "name": "x_studio_test_pos",
                "model_id": self.partner_model.id,
                "field_type": "char",
            }
        )
        # Selection field rejects values not in choices
        with self.assertRaises(ValueError):
            self.env["studio.light.view.injection"].create(
                {
                    "name": "Replace attempt",
                    "studio_field_id": f.id,
                    "model_id": self.partner_model.id,
                    "view_type": "form",
                    "target_field": "email",
                    "position": "replace",
                }
            )
