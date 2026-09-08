# -*- coding: utf-8 -*-
"""L'encre : ce que la main a tracé, gardé en nombres et rendu en SVG."""

import json
from datetime import datetime, timedelta

from odoo.exceptions import ValidationError
from odoo.tests import HttpCase, TransactionCase, tagged


TRAIT = [[[10, 20], [40, 60], [90, 30]], [[200, 200]]]


@tagged("post_install", "-at_install", "bf_celebrations")
class TestEncre(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.board = cls.env["bf.celebration.board"].create({
            "name": "Bonne fête",
            "recipient_partner_id": cls.env["res.partner"].create({
                "name": "Iris Bellefeuille", "email": "cel_ink@example.test",
            }).id,
            "delivery_date": datetime.now() + timedelta(days=2),
        })
        cls.Post = cls.env["bf.celebration.post"]

    def test_l_encre_est_relue_bornee_et_arrondie(self):
        brut = json.dumps([[[10.123, -5], [1000, 20.06]], []])
        propre = self.Post._normaliser_encre(brut)
        self.assertEqual(json.loads(propre),
                         [[[10.1, 0.0], [800.0, 20.1]]],
                         "Coordonnées bornées à la page, arrondies, trait "
                         "vide écarté.")

    def test_une_charge_tordue_est_refusee(self):
        for mauvais in ('{"a": 1}', "[[[1]]]", '[[["x", "y"]]]', "pas du json",
                        json.dumps([[[1, 2]]] * 401)):
            with self.assertRaises(ValueError, msg=mauvais[:30]):
                self.Post._normaliser_encre(mauvais)

    def test_trop_lourd_est_refuse(self):
        with self.assertRaises(ValueError):
            self.Post._normaliser_encre("[" + "1" * (300 * 1024) + "]")

    def test_un_tracé_seul_signe(self):
        """Ni texte ni image : le tracé suffit à ne pas être vide."""
        mot = self.Post.sudo().create({
            "board_id": self.board.id, "author_name": "Léonie",
            "ink_strokes": self.Post._normaliser_encre(json.dumps(TRAIT)),
        })
        self.assertTrue(mot.has_ink)
        with self.assertRaises(ValidationError):
            self.Post.sudo().create({
                "board_id": self.board.id, "author_name": "Personne"})

    def test_le_svg_suit_la_couleur_du_theme(self):
        mot = self.Post.sudo().create({
            "board_id": self.board.id, "author_name": "Léonie",
            "ink_strokes": self.Post._normaliser_encre(json.dumps(TRAIT)),
        })
        svg = str(mot.ink_svg())
        self.assertIn('stroke="currentColor"', svg,
                      "La couleur doit suivre le texte du thème du moment.")
        self.assertIn("<path d=\"M10.0 20.0Q40.0 60.0 65.0 45.0L90.0 30.0", svg)
        # Le point seul se rend quand même.
        self.assertIn("M200.0 200.0l0.1 0", svg)
        # Recadré : la boîte commence près du premier point, pas à 0.
        self.assertIn('viewBox="0.0 6.0', svg)
        # 🔴 Sans `width`/`height`, wkhtmltopdf rend le SVG à zéro : la carte
        # du PDF portait un blanc à la place du tracé.
        self.assertIn(' width="420" height=', svg)
        self.assertNotIn("<script", svg)

    def test_sans_encre_rien(self):
        mot = self.Post.sudo().create({
            "board_id": self.board.id, "author_name": "Léonie",
            "body": "<p>Bonne fête</p>"})
        self.assertFalse(mot.has_ink)
        self.assertEqual(str(mot.ink_svg()), "")


@tagged("post_install", "-at_install", "bf_celebrations")
class TestEncrePublique(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.board = cls.env["bf.celebration.board"].create({
            "name": "Bonne fête Iris",
            "recipient_partner_id": cls.env["res.partner"].create({
                "name": "Iris Bellefeuille", "email": "cel_ink2@example.test",
            }).id,
            "delivery_date": datetime.now() + timedelta(days=2),
            "state": "open",
        })
        cls.jeton = cls.board.sudo().access_token

    def test_signer_a_la_main_sans_compte(self):
        self.url_open(
            "/celebration/%s/signer" % self.jeton,
            data={"author_name": "Léonie", "body": "",
                  "ink": json.dumps(TRAIT), "style": "hand"},
            timeout=30)
        mot = self.board.sudo().post_ids
        self.assertEqual(len(mot), 1)
        self.assertTrue(mot.has_ink)
        self.assertEqual(mot.style, "hand")
        page = self.url_open("/celebration/%s" % self.jeton).text
        self.assertIn("<svg", page)
        self.assertIn('stroke="currentColor"', page)
        self.assertIn("cel-manuscrit", page)

    def test_une_encre_tordue_ne_cree_rien(self):
        reponse = self.url_open(
            "/celebration/%s/signer" % self.jeton,
            data={"author_name": "Léonie", "body": "",
                  "ink": '<svg onload="alert(1)"/>'},
            timeout=30)
        self.assertIn("erreur=encre", reponse.url)
        self.assertFalse(self.board.sudo().post_ids)

    def test_la_page_porte_le_canevas_et_la_police(self):
        page = self.url_open("/celebration/%s" % self.jeton).text
        self.assertIn('id="cel-encre"', page)
        self.assertIn("encre.js", page)
        css = self.url_open(
            "/bf_celebrations/static/src/css/celebrations_public.css").text
        self.assertIn("Caveat.woff2", css)
        police = self.url_open("/bf_celebrations/static/fonts/Caveat.woff2")
        self.assertEqual(police.status_code, 200)
        self.assertTrue(police.content.startswith(b"wOF2"))
