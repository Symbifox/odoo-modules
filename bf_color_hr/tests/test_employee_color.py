from odoo.tests import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestEmployeeColor(TransactionCase):
    def test_employee_carries_a_free_color(self):
        doctor = self.env["hr.employee"].create({"name": "Dr A", "color_hex": "#ff0000"})
        self.assertEqual(doctor.color_hex, "#FF0000")
        self.assertEqual(doctor.color_resolved, "#FF0000")
        self.assertIn("hr.employee", self.env["bf.color.mixin"]._bf_color_model_names())

    def test_people_without_hr_rights_still_read_employees(self):
        """Without HR rights, Odoo reads employees through the public profile;
        a stored field missing there made every prefetch fail."""
        doctor = self.env["hr.employee"].create({"name": "Dr A", "color_hex": "#FF0000"})
        clerk = new_test_user(self.env, "bfch_clerk", groups="base.group_user")
        seen = doctor.with_user(clerk)
        self.assertEqual(seen.name, "Dr A")
        self.assertEqual(seen.color_hex, "#FF0000")
        public = self.env["hr.employee.public"].with_user(clerk).browse(doctor.id)
        self.assertEqual(public.color_hex, "#FF0000")

    def test_employee_tag_carries_a_free_color(self):
        self.assertIn("hr.employee.category", self.env["bf.color.mixin"]._bf_color_model_names())
        tag = self.env["hr.employee.category"].create({"name": "On call", "color_hex": "#cc79a7"})
        self.assertEqual(tag.color_hex, "#CC79A7")
        self.assertEqual(tag.color_resolved, "#CC79A7")
        self.assertTrue(tag.color)

    def _arch(self, xmlid, view_type):
        view = self.env.ref(xmlid)
        return self.env[view.model].get_view(view.id, view_type)["arch"]

    def test_employee_tags_paint_the_free_color(self):
        for xmlid, kind in (("hr.view_employee_form", "form"), ("hr.view_employee_tree", "list"),
                            ("hr.hr_kanban_view_employees", "kanban")):
            self.assertIn("'bf_color': True", self._arch(xmlid, kind), xmlid)
        self.assertIn('widget="bf_color"', self._arch("hr.view_employee_category_list", "list"))
