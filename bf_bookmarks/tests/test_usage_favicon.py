# -*- coding: utf-8 -*-
import base64
import io
import ipaddress
import socket
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged

from odoo.addons.bf_bookmarks.models import bookmark_favicon as fav
from odoo.addons.bf_bookmarks.models.bookmark_usage import RECENT_LIMIT


def png_bytes(size=64, color=(200, 30, 30, 255), fmt="PNG"):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGBA", (size, size), color).save(buf, format=fmt)
    return buf.getvalue()


@tagged("post_install", "-at_install")
class TestUsage(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Users = cls.env["res.users"].with_context(no_reset_password=True)
        grp = cls.env.ref("base.group_user")
        cls.company = cls.env["res.company"].create({"name": "Société usage"})

        def make(login, groups=()):
            return Users.create({
                "name": login, "login": f"signets_usage_{login}", "company_id": cls.company.id,
                "company_ids": [(6, 0, [cls.company.id])],
                "groups_id": [(6, 0, [grp.id, *[g.id for g in groups]])]})

        cls.a = make("a")
        cls.b = make("b")
        cls.mgr = make("r", [cls.env.ref("bf_bookmarks.group_bookmark_manager")])
        Folder = cls.env["bf.bookmark.folder"]
        cls.root = Folder.search([("scope", "=", "company"), ("parent_id", "=", False),
                                  ("company_id", "=", cls.company.id)]) or Folder.create(
            {"scope": "company", "company_id": cls.company.id})
        cls.root.bookmark_ids.unlink()
        cls.marks = cls.env["bf.bookmark"].create([
            {"name": f"S{i}", "url": f"https://s{i}.example/", "folder_id": cls.root.id}
            for i in range(RECENT_LIMIT + 3)])

    def test_mark_used_and_recents_are_mine_only(self):
        self.marks[:3].with_user(self.a).action_mark_used()
        self.marks[0].with_user(self.a).action_mark_used()
        Usage = self.env["bf.bookmark.usage"]
        mine = Usage.with_user(self.a).search([])
        self.assertEqual(len(mine), 3)
        self.assertEqual(mine.filtered(lambda u: u.bookmark_id == self.marks[0]).use_count, 2)
        # 🔴 Personne d'autre ne voit mes usages, pas même un responsable des signets.
        self.assertFalse(Usage.with_user(self.b).search([]))
        self.assertFalse(Usage.with_user(self.mgr).search([]))
        data = self.env["bf.bookmark.folder"].with_user(self.b).widget_data()
        self.assertEqual(data["recents"], [])

    def test_recents_are_bounded_and_newest_first(self):
        for mark in self.marks:
            mark.with_user(self.a).action_mark_used()
        data = self.env["bf.bookmark.folder"].with_user(self.a).widget_data()
        self.assertEqual(len(data["recents"]), RECENT_LIMIT)
        self.assertEqual(data["recents"][0]["title"], self.marks[-1].name)

    def test_cannot_create_usage_for_someone_else(self):
        line = self.env["bf.bookmark.usage"].with_user(self.a).create(
            {"bookmark_id": self.marks[0].id, "user_id": self.b.id})
        self.assertEqual(line.user_id, self.a, "user_id forcé à l'appelant")

    def test_toggle_favorite_and_order(self):
        m0, m1, m2 = self.marks[:3]
        (m0 | m1 | m2).with_user(self.a).action_toggle_favorite()
        Bookmark = self.env["bf.bookmark"].with_user(self.a)
        Bookmark.set_favorite_order([m2.id, m0.id, m1.id])
        data = self.env["bf.bookmark.folder"].with_user(self.a).widget_data()
        self.assertEqual([f["title"] for f in data["favorites"]], [m2.name, m0.name, m1.name])
        self.assertTrue(all(f["favorite"] for f in data["favorites"]))
        m0.with_user(self.a).action_toggle_favorite()
        data = self.env["bf.bookmark.folder"].with_user(self.a).widget_data()
        self.assertEqual([f["title"] for f in data["favorites"]], [m2.name, m1.name])
        # L'ordre de A n'est pas celui de B.
        self.assertEqual(self.env["bf.bookmark.folder"].with_user(self.b).widget_data()["favorites"], [])

    def test_cannot_read_someone_elses_personal_bookmark_through_own_usage(self):
        """🔴 Relecture adverse : une ligne à soi vers le signet PERSO d'un autre."""
        root = self.env["bf.bookmark.folder"].with_user(self.a)._personal_root()
        secret = self.env["bf.bookmark"].with_user(self.a).create(
            {"name": "Secret de A", "url": "https://secret-a.example/", "folder_id": root.id})
        with self.assertRaises(AccessError):
            self.env["bf.bookmark.usage"].with_user(self.b).create({"bookmark_id": secret.id})
        # Même par une ligne existante de B qu'on repointe.
        self.marks[0].with_user(self.b).action_toggle_favorite()
        mine = self.env["bf.bookmark.usage"].with_user(self.b).search([])
        with self.assertRaises(AccessError):
            mine.write({"bookmark_id": secret.id})
        # Et si une telle ligne existait (posée en sudo), ses champs reliés ne parlent pas.
        planted = self.env["bf.bookmark.usage"].sudo().create(
            {"user_id": self.b.id, "bookmark_id": secret.id})
        self.env.invalidate_all()
        with self.assertRaises(AccessError):
            planted.with_user(self.b).read(["name", "url"])

    def test_shared_bookmarks_still_read_in_my_favorites(self):
        """Contre-épreuve du correctif : un signet d'entreprise se lit toujours
        normalement dans « Mes favoris » (champs reliés sous mes droits)."""
        self.marks[0].with_user(self.a).action_toggle_favorite()
        line = self.env["bf.bookmark.usage"].with_user(self.a).search([("favorite", "=", True)])
        self.env.invalidate_all()
        values = line.read(["name", "url", "folder_id"])[0]
        self.assertEqual((values["name"], values["url"]), (self.marks[0].name, self.marks[0].url))
        self.assertEqual(values["folder_id"][0], self.root.id)

    def test_cannot_move_own_usage_to_someone_else(self):
        self.marks[0].with_user(self.b).action_toggle_favorite()
        mine = self.env["bf.bookmark.usage"].with_user(self.b).search([])
        with self.assertRaises(AccessError):
            mine.write({"user_id": self.a.id})
        created = self.env["bf.bookmark.usage"].with_user(self.b).create(
            {"bookmark_id": self.marks[1].id, "user_id": self.a.id, "favorite": True})
        self.assertEqual(created.user_id, self.b, "user_id est toujours celui de l'appelant")
        data = self.env["bf.bookmark.folder"].with_user(self.a).widget_data()
        self.assertEqual(data["favorites"], [])

    def test_order_ignores_foreign_ids(self):
        self.marks[0].with_user(self.a).action_toggle_favorite()
        self.marks[1].with_user(self.b).action_toggle_favorite()
        self.env["bf.bookmark"].with_user(self.a).set_favorite_order([self.marks[1].id, self.marks[0].id])
        b_usage = self.env["bf.bookmark.usage"].search([("user_id", "=", self.b.id)])
        self.assertEqual(b_usage.favorite_sequence, 10, "l'ordre de B n'a pas bougé")
        self.assertFalse(self.env["bf.bookmark"].with_user(self.a).set_favorite_order("x"))

    def test_unreadable_bookmark_leaves_favorites(self):
        mark = self.marks[0]
        mark.with_user(self.a).action_toggle_favorite()
        dept = self.env["hr.department"].create({"name": "Fermé", "company_id": self.company.id})
        droot = self.env["bf.bookmark.folder"].create({"scope": "department", "department_id": dept.id})
        mark.folder_id = droot
        data = self.env["bf.bookmark.folder"].with_user(self.a).widget_data()
        self.assertEqual(data["favorites"], [])

    def test_mark_used_on_unreadable_does_nothing(self):
        dept = self.env["hr.department"].create({"name": "Fermé 2", "company_id": self.company.id})
        droot = self.env["bf.bookmark.folder"].create({"scope": "department", "department_id": dept.id})
        hidden = self.env["bf.bookmark"].create({"name": "Caché", "url": "https://cache.example/", "folder_id": droot.id})
        hidden.with_user(self.a).action_mark_used()
        self.assertFalse(self.env["bf.bookmark.usage"].sudo().search([("bookmark_id", "=", hidden.id)]))


@tagged("post_install", "-at_install")
class TestFavicon(TransactionCase):

    def test_public_ip_guard(self):
        for bad in ("127.0.0.1", "10.1.2.3", "192.168.1.1", "172.16.0.9", "169.254.169.254",
                    "100.64.1.1", "100.127.255.254", "0.0.0.0", "::1", "fe80::1", "fc00::1",
                    "::ffff:127.0.0.1", "::ffff:10.0.0.1", "224.0.0.1"):
            self.assertFalse(fav.ip_is_public(ipaddress.ip_address(bad)), bad)
        for good in ("8.8.8.8", "1.1.1.1", "2606:4700:4700::1111", "100.128.0.1"):
            self.assertTrue(fav.ip_is_public(ipaddress.ip_address(good)), good)

    def test_host_resolving_to_private_is_refused(self):
        def faux(host, port, **kw):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port)),
                    (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", port))]
        with patch.object(fav.socket, "getaddrinfo", faux):
            with self.assertRaises(fav.FetchRefused):
                fav.resolve_pinned_ip("melange.example", 443)
        with self.assertRaises(fav.FetchRefused):
            fav.http_get("ftp://x.example/", 1000)
        with self.assertRaises(fav.FetchRefused):
            fav.http_get("file:///etc/passwd", 1000)

    def test_redirect_to_private_is_refused(self):
        class Rep:
            def __init__(self, status, headers, body=b""):
                self.status, self.headers, self._b = status, headers, body
            def stream(self, n):
                yield self._b
            def release_conn(self):
                pass

        class Pool:
            def __init__(self, *a, **k):
                pass
            def request(self, *a, **k):
                return Rep(302, {"Location": "http://interne.example/secret"})
            def close(self):
                pass

        def faux(host, port, **kw):
            ip = "10.0.0.7" if host == "interne.example" else "93.184.216.34"
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]
        with patch.object(fav.socket, "getaddrinfo", faux), \
                patch.object(fav.urllib3, "HTTPSConnectionPool", Pool), \
                patch.object(fav.urllib3, "HTTPConnectionPool", Pool):
            with self.assertRaises(fav.FetchRefused):
                fav.http_get("https://public.example/", 1000)

    def test_icon_candidates(self):
        html = """<html><head>
            <link rel="icon" href="/big.png" sizes="192x192">
            <link rel="shortcut icon" href="/f32.ico" sizes="32x32">
            <link rel="icon" type="image/svg+xml" href="/v.svg">
            <link rel="apple-touch-icon" href="https://cdn.example/t.png">
            <link rel="icon" href="javascript:alert(1)">
            </head></html>"""
        cands = fav.icon_candidates("https://site.example/", html)
        self.assertEqual(cands[0], "https://site.example/f32.ico")
        self.assertIn("https://cdn.example/t.png", cands)
        self.assertNotIn("https://site.example/v.svg", cands)
        self.assertFalse(any(c.startswith("javascript") for c in cands))
        self.assertEqual(cands[-1], "https://site.example/favicon.ico")

    def test_to_png_normalizes(self):
        from PIL import Image
        for data in (png_bytes(64), png_bytes(16), png_bytes(48, fmt="ICO")):
            out = Image.open(io.BytesIO(fav.to_png(data)))
            self.assertEqual(out.size, (32, 32))
            self.assertEqual(out.format, "PNG")
        with self.assertRaises(fav.FetchRefused):
            fav.to_png(b"<svg></svg>")

    def _faux_web(self, pages):
        def get(_self, url, max_bytes, truncate=False):
            if url not in pages:
                raise fav.FetchRefused("absent")
            status, ctype, body = pages[url]
            return status, {"Content-Type": ctype}, body, url
        return patch.object(type(self.env["bf.bookmark.folder"].env["bf.bookmark.favicon"]), "_http_get", get)

    def test_fetch_and_serve_to_readers_only(self):
        company = self.env["res.company"].create({"name": "Société icônes"})
        root = self.env["bf.bookmark.folder"].search([("company_id", "=", company.id),
                                                      ("scope", "=", "company")]) or \
            self.env["bf.bookmark.folder"].create({"scope": "company", "company_id": company.id})
        mark = self.env["bf.bookmark"].create({"name": "Site", "url": "https://site.example/page",
                                               "folder_id": root.id})
        self.assertEqual(mark.host, "site.example")
        icon = self.env["bf.bookmark.favicon"].sudo().search([("host", "=", "site.example")])
        self.assertEqual(icon.state, "todo", "l'hôte est connu dès l'ajout, cherché par la tâche")
        pages = {
            "https://site.example/": (200, "text/html", b'<link rel="icon" href="/i.png">'),
            "https://site.example/i.png": (200, "image/png", png_bytes(64)),
        }
        with self._faux_web(pages):
            self.env["bf.bookmark.favicon"].with_context(bf_bookmarks_no_commit=True)._cron_refresh()
        self.assertEqual(icon.state, "ok")
        user = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Lecteur", "login": "signets_icone_lecteur", "company_id": company.id,
            "company_ids": [(6, 0, [company.id])],
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id])]})
        data = self.env["bf.bookmark.folder"].with_user(user).widget_data()
        node = [c for s in data["sections"] for c in s["children"] if c.get("url") == mark.url][0]
        # Une adresse par hôte, que le navigateur garde, plus l'image elle-même.
        self.assertEqual(node["icon"], icon._url())
        self.assertRegex(node["icon"], r"^/bf_bookmarks/icone/\d+/[0-9a-f]{32}\.png$")
        # 🔴 Le cache n'est pas lisible : il dirait quels sites les collègues ont en signets.
        with self.assertRaises(AccessError):
            self.env["bf.bookmark.favicon"].with_user(user).search([])
        outsider = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Ailleurs", "login": "signets_icone_ailleurs",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id])]})
        other = self.env["bf.bookmark.folder"].with_user(outsider).widget_data()
        self.assertNotIn("site.example", str(other))

    def test_fallback_to_favicon_ico_and_failure(self):
        mark = self.env["bf.bookmark"].create({"name": "Nu", "url": "https://nu.example/",
                                               "folder_id": self.env["bf.bookmark.folder"]._personal_root().id})
        icon = self.env["bf.bookmark.favicon"].sudo().search([("host", "=", mark.host)])
        with self._faux_web({"https://nu.example/favicon.ico": (200, "image/x-icon", png_bytes(32, fmt="ICO"))}):
            icon._fetch_one()
        self.assertEqual(icon.state, "ok")
        icon.write({"state": "todo", "image": False})
        with self._faux_web({"https://nu.example/": (200, "text/html", b"<html></html>")}):
            icon._fetch_one()
        self.assertEqual(icon.state, "none")

    def test_weekly_refresh_selection(self):
        Fav = self.env["bf.bookmark.favicon"].sudo()
        root = self.env["bf.bookmark.folder"]._personal_root()
        self.env["bf.bookmark"].create([
            {"name": h, "url": f"https://{h}/", "folder_id": root.id}
            for h in ("vieux.example", "frais.example", "echec.example")])
        now = fields.Datetime.now()
        Fav.search([("host", "=", "vieux.example")]).write({"state": "ok", "fetched_at": now - timedelta(days=8)})
        Fav.search([("host", "=", "frais.example")]).write({"state": "ok", "fetched_at": now - timedelta(days=2)})
        Fav.search([("host", "=", "echec.example")]).write({"state": "none", "fetched_at": now - timedelta(hours=2)})
        vus = []

        def compte(rec):
            vus.append(rec.host)
            return True
        with patch.object(type(Fav), "_fetch_one", compte):
            Fav.with_context(bf_bookmarks_no_commit=True)._cron_refresh()
        self.assertIn("vieux.example", vus)
        self.assertNotIn("frais.example", vus, "rafraîchie il y a 2 jours : pas avant 7")
        self.assertNotIn("echec.example", vus, "un échec se réessaie après un jour")

    def test_big_homepage_is_read_truncated(self):
        class Rep:
            status = 200
            headers = {"Content-Type": "text/html", "Content-Length": str(10 * 1024 * 1024)}
            def stream(self, n):
                yield b'<link rel="icon" href="/i.png">' + b"x" * (fav.MAX_PAGE_BYTES + 10)
            def release_conn(self):
                pass

        class Pool:
            def __init__(self, *a, **k):
                pass
            def request(self, *a, **k):
                return Rep()
            def close(self):
                pass

        def faux(host, port, **kw):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]
        with patch.object(fav.socket, "getaddrinfo", faux), \
                patch.object(fav.urllib3, "HTTPSConnectionPool", Pool):
            status, _h, body, _u = fav.http_get("https://gros.example/", fav.MAX_PAGE_BYTES, truncate=True)
            self.assertEqual(len(body), fav.MAX_PAGE_BYTES)
            with self.assertRaises(fav.FetchRefused):
                fav.http_get("https://gros.example/i.png", fav.MAX_ICON_BYTES)
