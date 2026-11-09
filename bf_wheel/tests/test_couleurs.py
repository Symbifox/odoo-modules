# -*- coding: utf-8 -*-
from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged

from odoo.addons.bf_live_base.models.color_utils import LIGHT_TEXT

# ⚠️ La palette et le calcul de contraste sont partis au socle (`bf_live_base`),
# et leurs essais avec eux. Ne restent ici que les trois qui parlent d'un
# SEGMENT : ce que la roue fait de la palette, et non ce que la palette vaut.


@tagged("post_install", "-at_install")
class TestCouleursSegments(TransactionCase):

    def test_une_couleur_fausse_est_refusee(self):
        roue = self.env["bf.wheel"].create({"name": "Couleurs"})
        with self.assertRaises(ValidationError):
            self.env["bf.wheel.segment"].create({
                "wheel_id": roue.id, "name": "Mauve", "color_hex": "mauve"})

    def test_les_segments_d_un_meme_geste_prennent_des_couleurs_differentes(self):
        roue = self.env["bf.wheel"].create({
            "name": "Quinze",
            "segment_ids": [(0, 0, {"name": "S%02d" % i}) for i in range(15)],
        })
        couleurs = roue.segment_ids.mapped("color_hex")
        self.assertEqual(len(set(couleurs[:12])), 12, "les douze premières diffèrent")
        self.assertEqual(couleurs[12], couleurs[0], "la treizième reprend la palette")

    def test_une_couleur_demandee_est_gardee(self):
        roue = self.env["bf.wheel"].create({
            "name": "Choisie",
            "segment_ids": [(0, 0, {"name": "Rouge maison", "color_hex": "#DA3246"})],
        })
        self.assertEqual(roue.segment_ids.color_hex, "#da3246")
        self.assertEqual(roue.segment_ids.color_display, "#da3246")
        self.assertEqual(roue.segment_ids.text_color, LIGHT_TEXT)
