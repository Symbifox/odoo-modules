from odoo.tests import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestProjectColors(TransactionCase):
    """Tasks colored by their tags."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = new_test_user(cls.env, "bfcp_user", groups="base.group_user,project.group_project_user")
        Tag = cls.env["project.tags"]
        cls.bizdev = Tag.create({"name": "Business development", "color": 0, "color_hex": "#E69F00"})
        cls.urgent = Tag.create({"name": "Urgent", "color": 0, "color_hex": "#D55E00"})
        cls.plain = Tag.create({"name": "Plain", "color": 0})
        cls.project = cls.env["project.project"].create({"name": "BFCP project"})
        Task = cls.env["project.task"]
        cls.both = Task.create({"name": "Both", "project_id": cls.project.id, "color": 0,
                                "tag_ids": [(6, 0, (cls.bizdev | cls.urgent).ids)]})
        cls.only_urgent = Task.create({"name": "Urgent only", "project_id": cls.project.id, "color": 0,
                                       "tag_ids": [(6, 0, cls.urgent.ids)]})
        cls.untagged = Task.create({"name": "Untagged", "project_id": cls.project.id, "color": 3})

    def _rule(self, **vals):
        return self.env["bf.color.rule"].create(dict({
            "name": "Tasks by tag",
            "model_id": self.env["ir.model"]._get("project.task").id,
            "field_id": self.env["ir.model.fields"]._get("project.task", "tag_ids").id,
        }, **vals))

    def test_tag_color_is_followed(self):
        self._rule()
        self.assertEqual(self.only_urgent.color_resolved, "#D55E00")
        # Without lines, the first tag (Odoo's order: by name) gives its color.
        self.assertEqual(self.both.color_resolved, "#E69F00")
        # No tag: the task keeps its own Odoo color.
        self.assertEqual(self.untagged.color_source, "record")

    def test_line_order_decides_between_tags(self):
        self._rule(line_ids=[
            (0, 0, {"value_res_id": self.urgent.id, "color_hex": "#D55E00", "sequence": 1}),
            (0, 0, {"value_res_id": self.bizdev.id, "color_hex": "#E69F00", "sequence": 2}),
        ])
        self.assertEqual(self.both.color_resolved, "#D55E00")

    def test_only_the_listed_tag_colors(self):
        """Only the listed tag, « Business development », colors the task."""
        self._rule(only_listed=True, line_ids=[
            (0, 0, {"value_res_id": self.bizdev.id, "color_hex": "#E69F00"}),
        ])
        self.assertEqual(self.both.color_resolved, "#E69F00")
        self.assertNotEqual(self.only_urgent.color_source, "rule")

    def test_user_sees_the_rule_color(self):
        self._rule()
        task = self.only_urgent.with_user(self.user)
        self.assertEqual(task.color_resolved, "#D55E00")

    def test_project_color_on_the_project(self):
        self.project.color_hex = "#0072B2"
        self.assertEqual(self.project.color_resolved, "#0072B2")
        # The Odoo index follows, for the views that only read it.
        self.assertTrue(self.project.color)
