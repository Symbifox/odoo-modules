from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestCalendarTypeColor(TransactionCase):
    def test_meeting_type_carries_a_free_color(self):
        self.assertIn("calendar.event.type", self.env["bf.color.mixin"]._bf_color_model_names())
        kind = self.env["calendar.event.type"].create({"name": "Clinic", "color_hex": "#0072b2"})
        self.assertEqual(kind.color_hex, "#0072B2")
        self.assertEqual(kind.color_resolved, "#0072B2")
        self.assertTrue(kind.color)

    def _arch(self, xmlid, view_type):
        view = self.env.ref(xmlid)
        return self.env[view.model].get_view(view.id, view_type)["arch"]

    def test_event_tags_paint_the_free_color(self):
        for xmlid, kind in (("calendar.view_calendar_event_form", "form"), ("calendar.view_calendar_event_tree", "list")):
            self.assertIn("'bf_color': True", self._arch(xmlid, kind), xmlid)
        self.assertIn('widget="bf_color"', self._arch("calendar.view_calendar_event_type_tree", "list"))
