"""Field lifecycle regression tests for bf_studio_light.

Covers create / read / write / unlink and the survival pattern (drop the
underlying ir.model.fields, run the integrity check, verify recovery).
"""

from odoo.tests.common import TransactionCase, tagged


@tagged("bf_studio_light", "post_install", "-at_install")
class TestStudioLightLifecycle(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner_model = cls.env["ir.model"]._get("res.partner")

    def test_create_simple_field_and_use(self):
        """Create a date field, write a value, read it back."""
        f = self.env["studio.light.field"].create(
            {
                "label": "Birth date",
                "name": "x_studio_birth_date_test",
                "model_id": self.partner_model.id,
                "field_type": "date",
            }
        )
        self.assertTrue(f.ir_model_field_id, "ir.model.fields should be created")
        self.assertEqual(f.ir_model_field_id.ttype, "date")
        self.assertEqual(f.ir_model_field_id.state, "manual")

        partner = self.env["res.partner"].create({"name": "Test partner"})
        partner.x_studio_birth_date_test = "1990-01-01"
        self.env.invalidate_all()
        self.assertEqual(
            str(self.env["res.partner"].browse(partner.id).x_studio_birth_date_test),
            "1990-01-01",
        )
        f.unlink()

    def test_related_field_no_compute_needed(self):
        """A related field should derive its value automatically."""
        f = self.env["studio.light.field"].create(
            {
                "label": "Parent name",
                "name": "x_studio_parent_name_test",
                "model_id": self.partner_model.id,
                "field_type": "char",
                "is_related": True,
                "related_path": "parent_id.name",
            }
        )
        self.assertEqual(f.ir_model_field_id.related, "parent_id.name")
        self.assertTrue(f.ir_model_field_id.readonly)

        parent = self.env["res.partner"].create({"name": "Parent Co."})
        child = self.env["res.partner"].create(
            {"name": "Child", "parent_id": parent.id}
        )
        self.assertEqual(child.x_studio_parent_name_test, "Parent Co.")
        f.unlink()

    def test_recovery_after_metadata_loss(self):
        """Drop the ir.model.fields row; integrity check must recreate it."""
        f = self.env["studio.light.field"].create(
            {
                "label": "Loss survivor",
                "name": "x_studio_survivor_test",
                "model_id": self.partner_model.id,
                "field_type": "char",
            }
        )
        original_imf = f.ir_model_field_id
        original_imf_name = original_imf.name
        # Detach our reference first so flushing is clean
        f.ir_model_field_id = False
        # Now drop the underlying record via ORM (not raw SQL)
        original_imf.unlink()
        self.env.invalidate_all()
        self.assertFalse(f.ir_model_field_id)

        self.env["studio.light.field"]._ensure_all_provisioned()
        self.env.invalidate_all()
        f = f.browse(f.id)  # re-read
        self.assertTrue(f.ir_model_field_id.exists())
        self.assertEqual(f.ir_model_field_id.name, original_imf_name)
        f.unlink()

    def test_binary_field_write_and_read(self):
        """A binary field stores and returns base64-encoded blobs."""
        import base64

        f = self.env["studio.light.field"].create(
            {
                "label": "Contract PDF",
                "name": "x_studio_contract_pdf_test",
                "model_id": self.partner_model.id,
                "field_type": "binary",
            }
        )
        self.assertTrue(f.ir_model_field_id)
        self.assertEqual(
            f.ir_model_field_id.ttype,
            "binary",
            "binary studio type must persist as ttype=binary",
        )

        payload = base64.b64encode(b"%PDF-1.4 fake content").decode()
        partner = self.env["res.partner"].create({"name": "Binary partner"})
        partner.x_studio_contract_pdf_test = payload
        self.env.invalidate_all()
        read_back = self.env["res.partner"].browse(partner.id).x_studio_contract_pdf_test
        # Odoo may return bytes or str depending on storage backend; normalise.
        if isinstance(read_back, bytes):
            read_back = read_back.decode()
        self.assertEqual(read_back, payload)
        f.unlink()

    def test_image_field_persists_as_binary_with_widget(self):
        """An image studio type stores as ttype=binary and the auto-generated
        view injection carries widget="image" so the renderer shows a
        thumbnail instead of a download link."""
        f = self.env["studio.light.field"].create(
            {
                "label": "Avatar custom",
                "name": "x_studio_avatar_custom_test",
                "model_id": self.partner_model.id,
                "field_type": "image",
            }
        )
        self.assertTrue(f.ir_model_field_id)
        self.assertEqual(
            f.ir_model_field_id.ttype,
            "binary",
            "image studio type must normalise to ttype=binary (Odoo has no image ttype)",
        )

        inj = self.env["studio.light.view.injection"].create(
            {
                "name": "Image injection test",
                "studio_field_id": f.id,
                "model_id": self.partner_model.id,
                "view_type": "form",
                "target_field": "name",
                "position": "after",
            }
        )
        self.assertTrue(inj.ir_view_id)
        self.assertIn(
            'widget="image"',
            inj.ir_view_id.arch,
            "image field injection must carry widget=image in the generated arch",
        )
        inj.unlink()
        f.unlink()

    def test_many2many_field_creation_and_assignment(self):
        """An m2m field stores a set of related records and can be
        written/read like any other m2m."""
        country_model = self.env["ir.model"]._get("res.country")
        f = self.env["studio.light.field"].create(
            {
                "label": "Markets served",
                "name": "x_studio_markets_test",
                "model_id": self.partner_model.id,
                "field_type": "many2many",
                "relation_model_id": country_model.id,
            }
        )
        self.assertTrue(f.ir_model_field_id)
        self.assertEqual(f.ir_model_field_id.ttype, "many2many")
        self.assertEqual(f.ir_model_field_id.relation, "res.country")

        ca = self.env.ref("base.ca")
        us = self.env.ref("base.us")
        partner = self.env["res.partner"].create({"name": "M2M partner"})
        partner.x_studio_markets_test = [(6, 0, [ca.id, us.id])]
        self.env.invalidate_all()
        read_back = self.env["res.partner"].browse(partner.id).x_studio_markets_test
        self.assertEqual(set(read_back.ids), {ca.id, us.id})
        f.unlink()

    def test_reference_field_with_whitelist(self):
        """A reference field with two whitelisted target models persists
        as ttype=reference and stores the whitelist as selection rows."""
        partner_model = self.partner_model
        country_model = self.env["ir.model"]._get("res.country")
        f = self.env["studio.light.field"].create(
            {
                "label": "Source record",
                "name": "x_studio_source_ref_test",
                "model_id": partner_model.id,
                "field_type": "reference",
                "reference_model_ids": [(6, 0, [partner_model.id, country_model.id])],
            }
        )
        self.assertTrue(f.ir_model_field_id)
        self.assertEqual(f.ir_model_field_id.ttype, "reference")
        whitelist = {s.value for s in f.ir_model_field_id.selection_ids}
        self.assertEqual(whitelist, {"res.partner", "res.country"})

        ca = self.env.ref("base.ca")
        partner = self.env["res.partner"].create({"name": "Ref partner"})
        partner.x_studio_source_ref_test = f"res.country,{ca.id}"
        self.env.invalidate_all()
        read_back = self.env["res.partner"].browse(partner.id).x_studio_source_ref_test
        self.assertEqual(read_back, ca)
        f.unlink()

    def test_modifier_expressions_emitted_in_arch(self):
        """When `invisible_expr` is set, the generated inheriting view
        arch must carry an `invisible="..."` attribute on the field tag
        so Odoo's view engine evaluates it at render time."""
        f = self.env["studio.light.field"].create(
            {
                "label": "Conditional field",
                "name": "x_studio_conditional_test",
                "model_id": self.partner_model.id,
                "field_type": "char",
                "invisible_expr": "is_company",
                "required_expr": "active",
            }
        )
        inj = self.env["studio.light.view.injection"].create(
            {
                "name": "Modifier injection test",
                "studio_field_id": f.id,
                "model_id": self.partner_model.id,
                "view_type": "form",
                "target_field": "name",
                "position": "after",
            }
        )
        arch = inj.ir_view_id.arch
        self.assertIn('invisible="is_company"', arch)
        self.assertIn('required="active"', arch)
        self.assertNotIn('readonly=', arch)
        inj.unlink()
        f.unlink()

    def test_modifier_expression_change_propagates_to_view(self):
        """Editing the modifier expression on the field must update the
        existing inheriting view arch — not wait for the next post-init
        cycle."""
        f = self.env["studio.light.field"].create(
            {
                "label": "Mutable modifier",
                "name": "x_studio_mutmod_test",
                "model_id": self.partner_model.id,
                "field_type": "char",
                "invisible_expr": "is_company",
            }
        )
        inj = self.env["studio.light.view.injection"].create(
            {
                "name": "Mut modifier injection",
                "studio_field_id": f.id,
                "model_id": self.partner_model.id,
                "view_type": "form",
                "target_field": "name",
                "position": "after",
            }
        )
        self.assertIn('invisible="is_company"', inj.ir_view_id.arch)
        f.invisible_expr = "not is_company"
        self.env.invalidate_all()
        self.assertIn('invisible="not is_company"', inj.ir_view_id.arch)
        inj.unlink()
        f.unlink()

    def test_failed_count_field_present(self):
        """The failed_count + auto-deactivate plumbing should be wired.

        The full failure-loop simulation is hard to set up cleanly in a
        TransactionCase (it requires forcing inconsistent SQL state that
        the ORM rejects on flush). We verify the fields exist and that
        the action_toggle_active resets the counter.
        """
        f = self.env["studio.light.field"].create(
            {
                "label": "Tracker check",
                "name": "x_studio_tracker_test",
                "model_id": self.partner_model.id,
                "field_type": "char",
            }
        )
        self.assertEqual(f.failed_count, 0)
        f.failed_count = 5
        f.active = False
        f.action_toggle_active()
        self.assertTrue(f.active)
        self.assertEqual(f.failed_count, 0, "Reactivation should clear failure counter")
        f.unlink()
