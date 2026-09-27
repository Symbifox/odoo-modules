from odoo.tests import TransactionCase, tagged

from ..models import color_utils as cu


@tagged("post_install", "-at_install")
class TestColorUtils(TransactionCase):
    def test_normalize(self):
        self.assertEqual(cu.normalize_hex("#abc"), "#AABBCC")
        self.assertEqual(cu.normalize_hex("ee2d2d"), "#EE2D2D")
        self.assertFalse(cu.normalize_hex("red"))
        self.assertFalse(cu.normalize_hex(""))
        self.assertFalse(cu.normalize_hex(None))

    def test_palette_round_trip(self):
        for index in range(1, 12):
            self.assertEqual(cu.nearest_index(cu.index_to_hex(index)), index)
        self.assertEqual(cu.nearest_index(False), 0)
        self.assertFalse(cu.index_to_hex(0))

    def test_nearest_index(self):
        self.assertEqual(cu.nearest_index("#FF0000"), 1)  # rouge
        self.assertEqual(cu.nearest_index("#00FF00"), 10)  # vert

    def test_text_color(self):
        self.assertEqual(cu.text_color("#FFFFFF"), "#000000")
        self.assertEqual(cu.text_color("#FCE89A"), "#000000")
        self.assertEqual(cu.text_color("#1F2A44"), "#FFFFFF")
        self.assertFalse(cu.text_color(False))

    def test_stable_pick(self):
        colors = ["#111111", "#222222", "#333333"]
        self.assertEqual(cu.stable_pick("a", colors), cu.stable_pick("a", colors))
        self.assertIn(cu.stable_pick("b", colors), colors)
        self.assertFalse(cu.stable_pick("a", []))
