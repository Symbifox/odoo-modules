from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestSkillTypeColor(TransactionCase):
    def test_skill_type_carries_a_free_color(self):
        self.assertIn("hr.skill.type", self.env["bf.color.mixin"]._bf_color_model_names())
        kind = self.env["hr.skill.type"].create({"name": "Languages", "color_hex": "#009e73"})
        self.assertEqual(kind.color_hex, "#009E73")
        # The skills copy the type's index (related): it follows the closest shade.
        skill = self.env["hr.skill"].create({"name": "Inuktitut", "skill_type_id": kind.id})
        self.assertEqual(skill.color, kind.color)
        self.assertTrue(kind.color)

    def _arch(self, xmlid, view_type):
        view = self.env.ref(xmlid)
        return self.env[view.model].get_view(view.id, view_type)["arch"]

    def test_skill_type_screens_offer_the_free_color(self):
        self.assertIn('widget="bf_color"', self._arch("hr_skills.hr_skill_type_view_tree", "list"))
        self.assertIn('widget="bf_color"', self._arch("hr_skills.hr_employee_skill_type_view_form", "form"))
