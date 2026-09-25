from odoo.tests import BaseCase

from ..lib import render


class TestRender(BaseCase):

    def test_seeded_avatar_is_stable_and_differs_by_name(self):
        for style in render.CHARACTER_STYLES:
            one = render.seed_config(style, "mireille fictive")
            self.assertEqual(one, render.seed_config(style, "mireille fictive"))
            others = {repr(render.seed_config(style, "person %d" % i)) for i in range(30)}
            self.assertGreater(len(others), 20, style)

    def test_seed_never_draws_an_excluded_part(self):
        for style in render.CHARACTER_STYLES:
            excluded = render.SEED_EXCLUDE[style]
            for i in range(300):
                config = render.seed_config(style, "seed %d" % i)
                for slot, parts in excluded.items():
                    if parts is None:
                        self.assertIsNone(config[slot], (style, slot))
                    else:
                        self.assertNotIn(config[slot], parts, (style, slot))

    def test_clean_config_drops_anything_the_style_does_not_know(self):
        hostile = {
            "head": '"/><script>alert(1)</script>',
            "face": "smile",
            "unknown": "x",
            "colors": {"skin": 'ffffff"/><script>', "clothing": "8fa7df"},
        }
        cleaned = render.clean_config("open_peeps", hostile, "seed")
        self.assertIn(cleaned["head"], render.load_style("open_peeps")["slots"]["head"])
        self.assertEqual(cleaned["face"], "smile")
        self.assertNotIn("unknown", cleaned)
        self.assertIn(cleaned["colors"]["skin"], render.load_style("open_peeps")["colors"]["skin"])
        self.assertEqual(cleaned["colors"]["clothing"], "8fa7df")
        svg = render.render_character("open_peeps", hostile, background='red"/><script>')
        self.assertNotIn("<script", svg)
        self.assertNotIn("@@", svg)

    def test_a_part_drawn_inside_a_part(self):
        # Notionists draws the badge on the outfit: the badge must show up.
        self.assertIn("bodyIcon", render.slot_order("notionists"))
        base = {"body": "variant05", "bodyIcon": "saturn"}
        with_badge = render.render_character("notionists", base)
        without = render.render_character("notionists", dict(base, bodyIcon=None))
        self.assertGreater(len(with_badge), len(without))
        self.assertNotIn("@@", with_badge)

    def test_initials_are_escaped_and_readable(self):
        svg = render.render_initials("<b>", "#ffffff")
        self.assertNotIn("<b>", svg.split("<text", 1)[1])
        for colour in render.initials_palette(["#ffcf77", "#1f84af", "#0D1B2A"]):
            self.assertGreaterEqual(render.contrast(colour, "#ffffff"), 4.5, colour)

    def test_generated_detection(self):
        odoo_svg = (
            "<?xml version='1.0' encoding='UTF-8' ?>"
            "<svg height='180' width='180' xmlns='http://www.w3.org/2000/svg' "
            "xmlns:xlink='http://www.w3.org/1999/xlink'>"
            "<rect fill='hsl(191, 52%, 45%)' height='180' width='180'/>"
            "<text fill='#ffffff' font-size='96' text-anchor='middle' x='90' y='125' "
            "font-family='sans-serif'>M</text></svg>")
        self.assertTrue(render.is_generated_svg(odoo_svg))
        self.assertTrue(render.is_generated_svg(render.render_initials("Marie", "#1f84af")))
        self.assertTrue(render.is_generated_svg(render.render_character("notionists", {})))
        uploaded = '<svg xmlns="http://www.w3.org/2000/svg"><circle r="4"/></svg>'
        self.assertFalse(render.is_generated_svg(uploaded))
        self.assertFalse(render.is_generated_svg(b"\x89PNG\r\n\x1a\n...."))
        # An upload that merely contains Odoo's header is not Odoo's avatar.
        self.assertFalse(render.is_generated_svg(odoo_svg.replace("</svg>", "<circle r='4'/></svg>")))
