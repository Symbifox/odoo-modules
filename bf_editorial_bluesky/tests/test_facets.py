# -*- coding: utf-8 -*-
"""Les positions du protocole AT sont en OCTETS UTF-8, pas en caractères.

Un billet français est plein d'accents. Compter en caractères décale chaque
lien de la ligne, et le lien devient cliquable au mauvais endroit. C'est
l'erreur classique de tout premier connecteur Bluesky.
"""

from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestFacets(TransactionCase):

    def setUp(self):
        super().setUp()
        self.co = self.env["bf.social.connector.bluesky"]

    def test_lien_sans_accent(self):
        t = "Read this https://example.com now"
        f = self.co._facets(t)
        self.assertEqual(len(f), 1)
        d, fin = f[0]["index"]["byteStart"], f[0]["index"]["byteEnd"]
        self.assertEqual(t.encode()[d:fin].decode(), "https://example.com")

    def test_lien_APRES_des_accents(self):
        """Le cas qui casse : « Récupérez » fait 10 caractères et 12 octets."""
        t = "Récupérez tout ça ici https://example.com/été"
        f = self.co._facets(t)
        self.assertEqual(len(f), 1)
        d, fin = f[0]["index"]["byteStart"], f[0]["index"]["byteEnd"]
        extrait = t.encode("utf-8")[d:fin].decode("utf-8")
        self.assertEqual(extrait, "https://example.com/été")
        self.assertNotEqual(d, t.index("https"),
                            "si les deux coïncident, le test ne prouve rien")

    def test_mot_clic_accentue(self):
        t = "Souveraineté numérique #données #privacy"
        f = self.co._facets(t)
        tags = [x["features"][0]["tag"] for x in f
                if x["features"][0]["$type"].endswith("#tag")]
        self.assertEqual(tags, ["données", "privacy"])
        for x in f:
            d, fin = x["index"]["byteStart"], x["index"]["byteEnd"]
            self.assertTrue(t.encode()[d:fin].decode().startswith("#"))

    def test_emoji_ne_decale_pas_la_suite(self):
        t = "🦊 Blue Fox https://bluefoxconsultant.com"
        f = self.co._facets(t)
        d, fin = f[0]["index"]["byteStart"], f[0]["index"]["byteEnd"]
        self.assertEqual(t.encode()[d:fin].decode(), "https://bluefoxconsultant.com")

    def test_aucun_lien_aucune_facette(self):
        self.assertEqual(self.co._facets("Juste du texte, sans rien."), [])

    def test_limite_annoncee(self):
        self.assertEqual(self.co._limits()["body_chars"], 300)
