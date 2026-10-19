# -*- coding: utf-8 -*-
from unittest.mock import patch

from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, tagged

from odoo.addons.bf_bookmarks.models import bookmark_links as links
from odoo.addons.bf_bookmarks.models.bookmark_favicon import FetchRefused, NotCheckable


@tagged("post_install", "-at_install")
class TestLinksProposals(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env["res.company"].create({"name": "Société liens"})
        Users = cls.env["res.users"].with_context(no_reset_password=True)
        grp = cls.env.ref("base.group_user")

        def make(login, groups=()):
            return Users.create({
                "name": login, "login": f"signets_liens_{login}", "company_id": cls.company.id,
                "company_ids": [(6, 0, [cls.company.id])],
                "groups_id": [(6, 0, [grp.id, *[g.id for g in groups]])]})

        cls.mgr = make("resp", [cls.env.ref("bf_bookmarks.group_bookmark_manager")])
        cls.chief = make("chef")
        cls.member = make("membre")
        cls.other = make("autre")
        Emp = cls.env["hr.employee"].with_company(cls.company)
        cls.dept = cls.env["hr.department"].create({"name": "Équipe liens", "company_id": cls.company.id})
        cls.dept.manager_id = Emp.create({"name": "Chef", "user_id": cls.chief.id, "department_id": cls.dept.id})
        Emp.create({"name": "Membre", "user_id": cls.member.id, "department_id": cls.dept.id})
        Folder = cls.env["bf.bookmark.folder"]
        cls.croot = Folder.search([("scope", "=", "company"), ("parent_id", "=", False),
                                   ("company_id", "=", cls.company.id)]) or Folder.create(
            {"scope": "company", "company_id": cls.company.id})
        cls.croot.bookmark_ids.unlink()
        cls.droot = Folder.create({"scope": "department", "department_id": cls.dept.id})

    # ------------------------------------------------------------------
    # Liens morts
    # ------------------------------------------------------------------

    def _probe_with(self, reponses):
        """reponses : url -> statut (int) ou exception à lever."""
        def faux(url, max_bytes, truncate=False):
            r = reponses[url]
            if isinstance(r, Exception):
                raise r
            return r, {}, b"", url
        return patch.object(links, "http_get", faux)

    def _check(self):
        return self.env["bf.bookmark"].with_context(bf_bookmarks_no_commit=True)._cron_check_links()

    def test_dead_after_two_failures_then_activity_to_the_right_person(self):
        B = self.env["bf.bookmark"]
        dmark = B.create({"name": "Doc équipe", "url": "https://mort.example/", "folder_id": self.droot.id})
        cmark = B.create({"name": "Doc société", "url": "https://mort2.example/", "folder_id": self.croot.id})
        rep = {dmark.url: 404, cmark.url: FetchRefused("résolution impossible")}
        with self._probe_with(rep):
            self._check()
        self.assertEqual(dmark.link_state, "unknown", "un seul échec ne suffit pas")
        self.assertEqual(dmark.link_failures, 1)
        (dmark | cmark).link_checked_at = False
        with self._probe_with(rep):
            self._check()
        self.assertEqual(dmark.link_state, "dead")
        self.assertEqual(cmark.link_state, "dead")
        self.assertEqual(dmark.activity_ids.user_id, self.chief, "le gestionnaire du département")
        # 🔴 Aucun courriel d'assignation : une tâche planifiée n'envoie rien d'elle-même.
        # Odoo le ferait par une notification sur le signet (et son courriel) : ni l'un ni
        # l'autre.
        self.assertFalse(self.env["mail.message"].sudo().search([
            ("model", "=", "bf.bookmark"), ("res_id", "in", (dmark | cmark).ids)]))
        self.assertFalse(self.env["mail.mail"].sudo().search([
            ("model", "=", "bf.bookmark"), ("res_id", "in", (dmark | cmark).ids)]))
        # Et l'ajout d'un signet par une vraie personne (pas le superutilisateur, qu'Odoo
        # n'abonne jamais) n'abonne personne.
        created = B.with_user(self.mgr).create({"name": "Z", "url": "https://z.example/",
                                                "folder_id": self.croot.id})
        self.assertFalse(created.sudo().message_follower_ids)
        self.assertEqual(cmark.activity_ids.user_id, self.mgr, "le responsable des signets de la société")
        # Une troisième passe ne double pas l'activité.
        (dmark | cmark).link_checked_at = False
        with self._probe_with(rep):
            self._check()
        self.assertEqual(len(dmark.activity_ids), 1)

    def test_login_pages_and_internal_hosts_are_never_dead(self):
        B = self.env["bf.bookmark"]
        m401 = B.create({"name": "Intranet", "url": "https://intra.example/", "folder_id": self.croot.id})
        m403 = B.create({"name": "Console", "url": "https://console.example/", "folder_id": self.croot.id})
        mint = B.create({"name": "Interne", "url": "http://10.0.0.8/", "folder_id": self.croot.id})
        rep = {m401.url: 401, m403.url: 403, mint.url: NotCheckable("adresse non publique")}
        for _i in range(3):
            (m401 | m403 | mint).link_checked_at = False
            with self._probe_with(rep):
                self._check()
        self.assertEqual((m401 | m403).mapped("link_state"), ["ok", "ok"])
        self.assertEqual(mint.link_state, "unknown")
        self.assertFalse((m401 | m403 | mint).activity_ids)

    def test_recovery_resets_the_count(self):
        m = self.env["bf.bookmark"].create({"name": "X", "url": "https://yoyo.example/", "folder_id": self.croot.id})
        with self._probe_with({m.url: 404}):
            self._check()
        m.link_checked_at = False
        with self._probe_with({m.url: 200}):
            self._check()
        self.assertEqual((m.link_state, m.link_failures), ("ok", 0))

    def test_personal_links_are_never_visited(self):
        root = self.env["bf.bookmark.folder"].with_user(self.member)._personal_root()
        m = self.env["bf.bookmark"].with_user(self.member).create(
            {"name": "Perso", "url": "https://perso-prive.example/", "folder_id": root.id})
        with self._probe_with({}):  # toute visite lèverait KeyError
            self._check()
        self.assertFalse(m.link_checked_at)

    def test_weekly_cadence(self):
        m = self.env["bf.bookmark"].create({"name": "Y", "url": "https://hebdo.example/", "folder_id": self.croot.id})
        with self._probe_with({m.url: 200}):
            self._check()
        with self._probe_with({}):  # déjà vérifié : pas revisité avant 7 jours
            self._check()
        self.assertEqual(m.link_state, "ok")

    # ------------------------------------------------------------------
    # Propositions
    # ------------------------------------------------------------------

    def test_propose_to_team_then_accept(self):
        P = self.env["bf.bookmark.proposal"].with_user(self.member)
        prop = P.create({"name": "Outil", "url": "https://outil.example/", "target_folder_id": self.droot.id})
        self.assertEqual(prop.responsible_id, self.chief)
        self.assertEqual(prop.activity_ids.user_id, self.chief)
        with self.assertRaises(AccessError):
            prop.action_accept()  # le proposant n'écrit pas dans l'équipe
        prop.with_user(self.chief).action_accept()
        self.assertEqual(prop.state, "accepted")
        self.assertEqual(prop.result_bookmark_id.folder_id, self.droot)
        self.assertEqual(prop.result_bookmark_id.create_uid, self.chief, "créé avec les droits du décideur")
        self.assertFalse(prop.activity_ids)

    def test_proposals_are_private_to_both_parties(self):
        prop = self.env["bf.bookmark.proposal"].with_user(self.member).create(
            {"name": "Outil", "url": "https://outil.example/", "target_folder_id": self.croot.id})
        self.assertEqual(prop.responsible_id, self.mgr)
        self.assertFalse(self.env["bf.bookmark.proposal"].with_user(self.other).search([("id", "=", prop.id)]))
        self.assertTrue(self.env["bf.bookmark.proposal"].with_user(self.mgr).search([("id", "=", prop.id)]))

    def test_protected_fields_cannot_be_forged(self):
        prop = self.env["bf.bookmark.proposal"].with_user(self.member).create(
            {"name": "Outil", "url": "https://outil.example/", "target_folder_id": self.croot.id,
             "responsible_id": self.member.id, "state": "accepted"})
        self.assertEqual((prop.state, prop.responsible_id), ("pending", self.mgr))
        for vals in ({"state": "accepted"}, {"responsible_id": self.member.id},
                     {"target_folder_id": self.droot.id}):
            with self.assertRaises(AccessError):
                prop.with_user(self.member).write(vals)

    def test_cannot_propose_to_invisible_or_personal(self):
        other_root = self.env["bf.bookmark.folder"].with_user(self.other)._personal_root()
        dept2 = self.env["hr.department"].create({"name": "Fermée", "company_id": self.company.id})
        closed = self.env["bf.bookmark.folder"].create({"scope": "department", "department_id": dept2.id})
        for target in (other_root, closed):
            with self.assertRaises(UserError):  # AccessError en hérite
                self.env["bf.bookmark.proposal"].with_user(self.member).create(
                    {"name": "x", "url": "https://x.example/", "target_folder_id": target.id})
        with self.assertRaises(Exception):
            self.env["bf.bookmark.proposal"].with_user(self.member).create(
                {"name": "x", "url": "javascript:alert(1)", "target_folder_id": self.croot.id})

    def test_proposal_origin_must_be_readable(self):
        root = self.env["bf.bookmark.folder"].with_user(self.other)._personal_root()
        secret = self.env["bf.bookmark"].with_user(self.other).create(
            {"name": "Secret autre", "url": "https://secret-autre.example/", "folder_id": root.id})
        with self.assertRaises(AccessError):
            self.env["bf.bookmark.proposal"].with_user(self.member).create(
                {"name": "x", "url": "https://x.example/", "target_folder_id": self.croot.id,
                 "bookmark_id": secret.id})

    def test_refuse(self):
        prop = self.env["bf.bookmark.proposal"].with_user(self.member).create(
            {"name": "Non", "url": "https://non.example/", "target_folder_id": self.droot.id})
        prop.with_user(self.chief).action_refuse()
        self.assertEqual(prop.state, "refused")
        with self.assertRaises(UserError):
            prop.with_user(self.chief).action_accept()

    # ------------------------------------------------------------------
    # Import depuis le navigateur
    # ------------------------------------------------------------------

    def test_import_from_browser(self):
        Import = self.env["bf.bookmark.import"].with_user(self.member)
        nodes = [
            {"title": "Barre", "children": [
                {"title": "Odoo", "url": "https://odoo.example/"},
                {"title": "Clients", "children": [{"title": "A", "url": "https://a.example/"}]},
                {"title": "Piège", "url": "javascript:alert(1)"},
            ]},
            {"title": "Seul", "url": "https://seul.example/"},
            "pas un nœud", {"title": "vide"},
        ]
        stats = Import.import_from_browser(nodes)
        self.assertEqual(stats, {"folders": 2, "bookmarks": 3, "skipped": 0, "refused": 1})
        root = self.env["bf.bookmark.folder"].with_user(self.member)._personal_root()
        target = root.child_ids.filtered(lambda f: f.name == "Importés du navigateur")
        self.assertEqual(len(target), 1)
        self.assertEqual(target.child_ids.name, "Barre")
        again = Import.import_from_browser(nodes)
        self.assertEqual(again["bookmarks"], 0)
        self.assertEqual(again["skipped"], 3)
        self.assertEqual(len(root.child_ids.filtered(lambda f: f.name == "Importés du navigateur")), 1)

    def test_import_from_browser_flattens_too_deep(self):
        from odoo.addons.bf_bookmarks.models.bookmark_folder import MAX_DEPTH
        node = {"title": "Fond", "url": "https://fond.example/"}
        for level in range(MAX_DEPTH + 5):
            node = {"title": f"N{level}", "children": [node]}
        stats = self.env["bf.bookmark.import"].with_user(self.member).import_from_browser([node])
        self.assertEqual(stats["bookmarks"], 1, "le signet profond est gardé, la hiérarchie aplatie")
