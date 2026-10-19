# -*- coding: utf-8 -*-
"""Les icônes servies par adresse, gardées par le navigateur.

L'adresse porte un jeton signé lié à l'image. Seul `widget_data` la donne, et
seulement pour des signets lus avec les droits de la personne : on ne peut pas la
fabriquer pour un hôte qu'on n'a pas en signet.
"""
import base64

from odoo.tests import HttpCase, new_test_user, tagged

from .test_usage_favicon import png_bytes


@tagged("post_install", "-at_install")
class TestIconRoute(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env["res.company"].create({"name": "Société adresses d'icônes"})
        Folder = cls.env["bf.bookmark.folder"]
        cls.root = Folder.search([("company_id", "=", cls.company.id), ("scope", "=", "company")]) \
            or Folder.create({"scope": "company", "company_id": cls.company.id})
        cls.mark = cls.env["bf.bookmark"].create(
            {"name": "Rouge", "url": "https://rouge.example/", "folder_id": cls.root.id})
        cls.other = cls.env["bf.bookmark"].create(
            {"name": "Bleu", "url": "https://bleu.example/", "folder_id": cls.root.id})
        Favicon = cls.env["bf.bookmark.favicon"].sudo()
        cls.red = Favicon.search([("host", "=", "rouge.example")])
        cls.blue = Favicon.search([("host", "=", "bleu.example")])
        cls.red_png = png_bytes(32, (200, 30, 30, 255))
        cls.red.write({"image": base64.b64encode(cls.red_png), "state": "ok"})
        cls.blue.write({"image": base64.b64encode(png_bytes(32, (30, 30, 200, 255))), "state": "ok"})
        cls.user = new_test_user(cls.env, login="signets_adresse", password="signets_adresse",
                                 groups="base.group_user", company_id=cls.company.id,
                                 company_ids=[(6, 0, [cls.company.id])])

    def _icon_of(self, user, bookmark):
        data = self.env["bf.bookmark.folder"].with_user(user).widget_data()
        nodes = [c for s in data["sections"] for c in s["children"] if c.get("url") == bookmark.url]
        return nodes[0]["icon"] if nodes else None

    def test_icon_is_served_and_cached(self):
        url = self._icon_of(self.user, self.mark)
        self.assertTrue(url)
        self.authenticate("signets_adresse", "signets_adresse")
        resp = self.url_open(url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers["Content-Type"], "image/png")
        self.assertEqual(resp.content, self.red_png)
        cache = resp.headers.get("Cache-Control", "")
        self.assertIn("immutable", cache)
        self.assertIn("private", cache, "jamais gardée par un mandataire partagé")
        self.assertEqual(resp.headers.get("X-Content-Type-Options"), "nosniff")

    def test_token_is_bound_to_the_icon(self):
        """🔴 Le jeton d'une icône n'ouvre pas une autre : sans quoi on parcourrait les
        hôtes du cache, donc les sites que les collègues ont en signets."""
        self.authenticate("signets_adresse", "signets_adresse")
        red = self.red._url()
        token = red.rsplit("/", 1)[1]
        self.assertEqual(self.url_open(f"/bf_bookmarks/icone/{self.blue.id}/{token}").status_code, 404)
        self.assertEqual(self.url_open(f"/bf_bookmarks/icone/{self.red.id}/{'0' * 32}.png").status_code, 404)
        self.assertEqual(self.url_open(f"/bf_bookmarks/icone/{self.red.id}/{token[:-1]}.png").status_code, 404)
        self.assertEqual(self.url_open("/bf_bookmarks/icone/999999999/" + token).status_code, 404)

    def test_new_image_gets_new_address(self):
        """L'adresse se garde sans limite parce qu'elle change avec l'image."""
        before = self.red._url()
        self.red.write({"image": base64.b64encode(png_bytes(32, (10, 200, 10, 255)))})
        after = self.red._url()
        self.assertNotEqual(before, after)
        self.authenticate("signets_adresse", "signets_adresse")
        self.assertEqual(self.url_open(before).status_code, 404)
        self.assertEqual(self.url_open(after).status_code, 200)

    def test_failed_icon_is_not_served(self):
        url = self.blue._url()
        self.blue.write({"state": "none"})
        self.authenticate("signets_adresse", "signets_adresse")
        self.assertEqual(self.url_open(url).status_code, 404)

    def test_anonymous_gets_no_icon(self):
        resp = self.url_open(self.red._url(), allow_redirects=False)
        self.assertNotEqual(resp.status_code, 200)
        self.assertNotEqual(resp.headers.get("Content-Type"), "image/png")

    def test_unreadable_bookmark_gives_no_address(self):
        """Hors de la société : le signet n'est pas lu, donc aucune adresse d'icône."""
        outsider = new_test_user(self.env, login="signets_adresse_ailleurs", groups="base.group_user")
        data = self.env["bf.bookmark.folder"].with_user(outsider).widget_data()
        self.assertNotIn(f"/bf_bookmarks/icone/{self.red.id}/", str(data))
        self.assertNotIn("rouge.example", str(data))
