# -*- coding: utf-8 -*-
"""Relecture adverse avant publication (18.0.1.5.1).

Chaque essai rejoue une attaque par RPC : l'appelant choisit les valeurs ET le
contexte (`default_*` compris). Les correctifs tiennent sur ce que fait la base,
pas sur ce qu'affiche le formulaire.
"""
from unittest.mock import patch

from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, tagged

from odoo.addons.bf_bookmarks.models import bookmark_favicon as fav
from odoo.addons.bf_bookmarks.models import bookmark_links as links


@tagged("post_install", "-at_install")
class TestRelectureAdverse(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env["res.company"].create({"name": "Société relecture"})
        cls.other_company = cls.env["res.company"].create({"name": "Société voisine"})
        Users = cls.env["res.users"].with_context(no_reset_password=True)
        grp = cls.env.ref("base.group_user")

        def make(login, groups=()):
            return Users.create({
                "name": login, "login": f"signets_relecture_{login}", "company_id": cls.company.id,
                "company_ids": [(6, 0, [cls.company.id])],
                "groups_id": [(6, 0, [grp.id, *[g.id for g in groups]])]})

        cls.a = make("a")
        cls.b = make("b")
        cls.mgr = make("resp", [cls.env.ref("bf_bookmarks.group_bookmark_manager")])
        Folder = cls.env["bf.bookmark.folder"]
        cls.croot = Folder.search([("scope", "=", "company"), ("parent_id", "=", False),
                                   ("company_id", "=", cls.company.id)]) or Folder.create(
            {"scope": "company", "company_id": cls.company.id})
        cls.root_a = Folder.with_user(cls.a)._personal_root()
        cls.root_b = Folder.with_user(cls.b)._personal_root()
        cls.mark_a = cls.env["bf.bookmark"].with_user(cls.a).create(
            {"name": "Hameçon", "url": "https://piege.example/", "folder_id": cls.root_a.id})
        cls.secret_b = cls.env["bf.bookmark"].with_user(cls.b).create(
            {"name": "Titre secret de B", "url": "https://b.example/", "folder_id": cls.root_b.id})
        cls.folder_b = Folder.with_user(cls.b).create(
            {"name": "Dossier secret de B", "parent_id": cls.root_b.id})

    def _visible_to_b(self, mark):
        self.env.invalidate_all()
        return bool(self.env["bf.bookmark"].with_user(self.b).search([("id", "=", mark.id)]))

    # ------------------------------------------------------------------
    # Un signet perso publié à la société par ses champs recopiés
    # ------------------------------------------------------------------

    def test_derived_fields_cannot_be_written(self):
        """🔴 L'attaque reproduite : `scope`/`company_id` écrits sur son propre perso."""
        attempts = [
            {"scope": "company", "company_id": self.company.id, "owner_id": False},
            {"scope": "company", "company_id": self.other_company.id, "owner_id": False},
            {"owner_id": self.b.id},
            {"department_id": self.env["hr.department"].create({"name": "D"}).id},
            {"project_id": self.env["project.project"].create({"name": "P"}).id},
            {"host": "b.example"},
        ]
        for vals in attempts:
            with self.subTest(vals=vals), self.assertRaises(AccessError):
                self.mark_a.with_user(self.a).write(vals)
        self.assertEqual(self.mark_a.scope, "personal")
        self.assertFalse(self._visible_to_b(self.mark_a))

    def test_derived_fields_are_ignored_at_creation(self):
        """Par les valeurs comme par un `default_*` du contexte."""
        B = self.env["bf.bookmark"].with_user(self.a)
        by_vals = B.create({"name": "V", "url": "https://v.example/", "folder_id": self.root_a.id,
                            "scope": "company", "company_id": self.company.id, "owner_id": False,
                            "host": "b.example"})
        by_ctx = B.with_context(default_scope="company", default_company_id=self.company.id,
                                default_owner_id=False, default_host="b.example").create(
            {"name": "C", "url": "https://c.example/", "folder_id": self.root_a.id})
        for mark in by_vals | by_ctx:
            self.assertEqual((mark.scope, mark.owner_id), ("personal", self.a))
            self.assertFalse(self._visible_to_b(mark))
        self.assertEqual((by_vals.host, by_ctx.host), ("v.example", "c.example"))

    def test_moving_still_works_and_stays_consistent(self):
        """Contre-épreuve : déplacer par `folder_id` reste le chemin, et les champs suivent."""
        mark = self.env["bf.bookmark"].with_user(self.mgr).create(
            {"name": "M", "url": "https://m.example/", "folder_id": self.croot.id})
        self.assertEqual(mark.scope, "company")
        mark.with_user(self.mgr).write({"url": "https://n.example/page"})
        self.assertEqual(mark.host, "n.example")
        sub = self.env["bf.bookmark.folder"].with_user(self.mgr).create(
            {"name": "Sous", "parent_id": self.croot.id})
        mark.with_user(self.mgr).write({"folder_id": sub.id})
        self.assertEqual((mark.scope, mark.company_id), ("company", self.company))

    def test_inconsistent_values_are_refused_even_in_sudo(self):
        """Le filet ne dépend pas de l'appelant : la fiche dit ce que dit son dossier."""
        with self.assertRaises(AccessError):
            self.mark_a.sudo().write({"scope": "company", "company_id": self.company.id})

    # ------------------------------------------------------------------
    # Le titre d'un signet perso d'autrui par une proposition
    # ------------------------------------------------------------------

    def test_proposal_cannot_point_its_result_at_someone_elses_bookmark(self):
        P = self.env["bf.bookmark.proposal"].with_user(self.a)
        base = {"name": "x", "url": "https://x.example/", "target_folder_id": self.croot.id}
        by_vals = P.create(dict(base, result_bookmark_id=self.secret_b.id))
        by_ctx = P.with_context(default_result_bookmark_id=self.secret_b.id,
                                default_state="accepted").create(dict(base))
        self.env.invalidate_all()
        for proposal in by_vals | by_ctx:
            values = proposal.with_user(self.a).read(["result_bookmark_id", "state"])[0]
            self.assertEqual((values["result_bookmark_id"], values["state"]), (False, "pending"))

    def test_proposal_name_and_url_are_frozen(self):
        prop = self.env["bf.bookmark.proposal"].with_user(self.a).create(
            {"name": "Doc", "url": "https://doc.example/", "target_folder_id": self.croot.id})
        for vals in ({"url": "javascript:alert(1)"}, {"url": "https://ailleurs.example/"},
                     {"name": "Autre chose"}, {"note": "Autre note"}):
            with self.subTest(vals=vals), self.assertRaises(AccessError):
                prop.with_user(self.a).write(vals)

    def test_activity_notes_are_escaped(self):
        """Titre et adresse viennent de l'usager ; la note de l'activité est du HTML."""
        piege = '<a href="https://piege.example/">Cliquez</a><img src="https://espion.example/p.png">'
        prop = self.env["bf.bookmark.proposal"].with_user(self.a).create(
            {"name": piege, "url": "https://x.example/", "target_folder_id": self.croot.id})
        note = str(prop.sudo().activity_ids.note)
        self.assertNotIn("<img", note)
        self.assertNotIn('<a href="https://piege.example/', note)
        self.assertIn("&lt;img", note)
        mark = self.env["bf.bookmark"].create(
            {"name": piege, "url": "https://mort.example/", "folder_id": self.croot.id})
        mark._notify_dead_link("statut 404")
        note = str(mark.sudo().activity_ids.note)
        self.assertNotIn("<img", note)
        self.assertIn("&lt;img", note)

    # ------------------------------------------------------------------
    # Le nom d'un dossier perso d'autrui par l'assistant d'import
    # ------------------------------------------------------------------

    def test_import_wizard_refuses_a_folder_it_cannot_write(self):
        W = self.env["bf.bookmark.import"].with_user(self.a)
        with self.assertRaises(AccessError):
            W.create({"folder_id": self.folder_b.id, "file": b"eA=="})
        with self.assertRaises(AccessError):
            W.with_context(default_folder_id=self.folder_b.id).create({"file": b"eA=="})
        with self.assertRaises(AccessError):
            W.create({"folder_id": self.croot.id, "file": b"eA=="})
        mine = W.create({"file": b"eA=="})
        self.assertEqual(mine.folder_id, self.root_a)
        with self.assertRaises(AccessError):
            mine.write({"folder_id": self.folder_b.id})

    # ------------------------------------------------------------------
    # La vérification des liens n'est pas un balayeur de ports
    # ------------------------------------------------------------------

    def test_only_standard_ports_are_fetched(self):
        def resolution(host, port, proto=0):
            raise AssertionError("aucune résolution pour un port non standard")
        with patch.object(fav.socket, "getaddrinfo", resolution):
            for url in ("https://public.example:8443/", "http://public.example:22/"):
                with self.subTest(url=url), self.assertRaises(fav.NotCheckable):
                    fav.http_get(url, 1000)
        alive, note = self.env["bf.bookmark"]._probe("https://public.example:8443/")
        self.assertIsNone(alive, "ni vivant ni mort : non vérifié")

    def test_failure_note_does_not_name_the_exception(self):
        def refused(url, max_bytes, truncate=False):
            raise ConnectionRefusedError("refusée")
        with patch.object(links, "http_get", refused):
            alive, note = self.env["bf.bookmark"]._probe("https://public.example/")
        self.assertFalse(alive)
        self.assertNotIn("ConnectionRefused", note)

    # ------------------------------------------------------------------
    # L'adresse, quel que soit le chemin
    # ------------------------------------------------------------------

    def test_url_scheme_holds_on_every_path(self):
        """🔴 `default_url` dans le contexte ne passait pas par le nettoyage : un
        responsable distribuait `javascript:` à tout le personnel par l'extension."""
        piege = "javascript:alert(document.domain)//"
        B = self.env["bf.bookmark"].with_user(self.mgr)
        with self.assertRaises(ValidationError):
            B.with_context(default_url=piege).create({"name": "Intranet", "folder_id": self.croot.id})
        with self.assertRaises(ValidationError):
            self.croot.with_user(self.mgr).with_context(default_url=piege).write(
                {"bookmark_ids": [(0, 0, {"name": "Paie"})]})
        mark = B.create({"name": "Sain", "url": "https://sain.example/", "folder_id": self.croot.id})
        with self.assertRaises(ValidationError):
            mark.copy({"url": "file://hote/partage"})
        with self.assertRaises(ValidationError):
            self.env["bf.bookmark"].with_user(self.a).with_context(default_url="file://hote/partage").create(
                {"name": "Perso", "folder_id": self.root_a.id})

    # ------------------------------------------------------------------
    # Le formulaire ne nomme que ce qu'on peut lire
    # ------------------------------------------------------------------

    def _onchange(self, model, values=None, ctx=None, record=None, user=None):
        target = (record or self.env[model]).with_user(user or self.a).with_context(**(ctx or {}))
        spec = {name: {"fields": {"display_name": {}}} if f.type == "many2one" else {}
                for name, f in target._fields.items()
                if name in ("folder_id", "parent_id", "bookmark_id", "result_bookmark_id",
                            "target_folder_id", "complete_name", "owner_id", "project_id")}
        return target.onchange(values or {}, [], spec)

    def test_onchange_names_nothing_unreadable(self):
        """🔴 `onchange` rendait le nom d'un signet ou d'un dossier perso d'autrui, par
        un `default_*` comme par les valeurs, sans rien créer."""
        attaques = [
            ("bf.bookmark.proposal", "result_bookmark_id", self.secret_b),
            ("bf.bookmark.proposal", "bookmark_id", self.secret_b),
            ("bf.bookmark.proposal", "target_folder_id", self.folder_b),
            ("bf.bookmark.usage", "bookmark_id", self.secret_b),
            ("bf.bookmark.import", "folder_id", self.folder_b),
            ("bf.bookmark.folder", "parent_id", self.folder_b),
            ("bf.bookmark", "folder_id", self.folder_b),
        ]
        for model, field, target in attaques:
            for how in ("ctx", "values"):
                with self.subTest(model=model, field=field, how=how), self.assertRaises(AccessError):
                    if how == "ctx":
                        self._onchange(model, ctx={f"default_{field}": target.id})
                    else:
                        self._onchange(model, values={field: target.id})

    def test_onchange_still_works_where_one_may_read(self):
        """Contre-épreuve : les formulaires ordinaires s'ouvrent comme avant."""
        r = self._onchange("bf.bookmark", ctx={"default_folder_id": self.root_a.id})
        self.assertEqual(r["value"]["folder_id"]["id"], self.root_a.id)
        r = self._onchange("bf.bookmark.proposal", ctx={"default_bookmark_id": self.mark_a.id,
                                                        "default_name": "Hameçon"})
        self.assertEqual(r["value"]["bookmark_id"]["id"], self.mark_a.id)
        self._onchange("bf.bookmark.import", ctx={"default_folder_id": self.root_a.id})
        # Le responsable ouvre une proposition reçue : son signet d'origine est le perso
        # du proposant, illisible pour lui, mais c'est la valeur que la fiche porte déjà.
        prop = self.env["bf.bookmark.proposal"].with_user(self.a).create(
            {"name": "Doc", "url": "https://doc.example/", "target_folder_id": self.croot.id,
             "bookmark_id": self.mark_a.id})
        self._onchange("bf.bookmark.proposal", record=prop, user=self.mgr,
                       values={"bookmark_id": self.mark_a.id, "target_folder_id": self.croot.id})

    # ------------------------------------------------------------------
    # Ses propres valeurs par défaut (ir.default), posées par RPC
    # ------------------------------------------------------------------

    def test_personal_defaults_name_nothing_unreadable(self):
        """🔴 Chacun peut se poser des valeurs par défaut : `onchange` et `create` les
        servent. Elles passaient sous le garde, qui ne lisait que valeurs et contexte."""
        Default = self.env["ir.default"].with_user(self.a)
        cas = [
            ("bf.bookmark.proposal", "bookmark_id", self.secret_b),
            ("bf.bookmark.proposal", "target_folder_id", self.folder_b),
            ("bf.bookmark.usage", "bookmark_id", self.secret_b),
            ("bf.bookmark.import", "folder_id", self.folder_b),
            ("bf.bookmark.folder", "parent_id", self.folder_b),
            ("bf.bookmark", "folder_id", self.folder_b),
        ]
        for model, field, target in cas:
            with self.subTest(model=model, field=field):
                Default.set(model, field, target.id, user_id=True)
                try:
                    with self.assertRaises(AccessError):
                        self._onchange(model)
                finally:
                    Default.sudo().search([("field_id.model", "=", model),
                                           ("field_id.name", "=", field)]).unlink()

    def test_proposal_ignores_personal_defaults(self):
        Default = self.env["ir.default"].with_user(self.a)
        Default.set("bf.bookmark.proposal", "state", "accepted", user_id=True)
        Default.set("bf.bookmark.proposal", "result_bookmark_id", self.secret_b.id, user_id=True)
        Default.set("bf.bookmark.proposal", "bookmark_id", self.secret_b.id, user_id=True)
        prop = self.env["bf.bookmark.proposal"].with_user(self.a).create(
            {"name": "x", "url": "https://x.example/", "target_folder_id": self.croot.id})
        self.env.invalidate_all()
        values = prop.with_user(self.a).read(["state", "result_bookmark_id", "bookmark_id"])[0]
        self.assertEqual((values["state"], values["result_bookmark_id"], values["bookmark_id"]),
                         ("pending", False, False))

    # ------------------------------------------------------------------
    # Un favori ne survit pas à la perte d'accès
    # ------------------------------------------------------------------

    def test_stale_favorite_says_nothing(self):
        """🔴 Signet d'entreprise mis en favori par A, puis déplacé par le responsable
        dans SON perso et renommé : la ligne de A rendait le nouveau titre et l'adresse."""
        mark = self.env["bf.bookmark"].with_user(self.mgr).create(
            {"name": "Doc commune", "url": "https://doc.example/", "folder_id": self.croot.id})
        mark.with_user(self.a).action_toggle_favorite()
        root_mgr = self.env["bf.bookmark.folder"].with_user(self.mgr)._personal_root()
        mark.with_user(self.mgr).write({"folder_id": root_mgr.id})
        mark.with_user(self.mgr).write({"name": "PERSO-RENOMME", "url": "https://prive.example/secret"})
        self.env.invalidate_all()
        Usage = self.env["bf.bookmark.usage"].with_user(self.a)
        self.assertFalse(Usage.search([]))
        self.assertNotIn("PERSO-RENOMME", str(Usage.read_group([], ["bookmark_id"], ["bookmark_id"])))
        line = self.env["bf.bookmark.usage"].sudo().search(
            [("user_id", "=", self.a.id), ("bookmark_id", "=", mark.id)])
        with self.assertRaises(AccessError):
            line.with_user(self.a).read(["bookmark_id", "url"])
        data = self.env["bf.bookmark.folder"].with_user(self.a).widget_data()
        self.assertNotIn("prive.example", str(data))

    def test_usage_visibility_matches_bookmark_visibility(self):
        """Les règles des lignes d'usage recopient celles des signets : une ligne n'est
        visible que si son signet l'est, portée par portée (filet contre une dérive)."""
        Folder = self.env["bf.bookmark.folder"]
        Emp = self.env["hr.employee"].with_company(self.company)
        dept_in = self.env["hr.department"].create({"name": "Avec A", "company_id": self.company.id})
        Emp.create({"name": "A", "user_id": self.a.id, "department_id": dept_in.id})
        dept_out = self.env["hr.department"].create({"name": "Sans A", "company_id": self.company.id})
        proj_in = self.env["project.project"].create({"name": "Suivi par A", "company_id": self.company.id})
        proj_in.message_subscribe(partner_ids=self.a.partner_id.ids)
        proj_out = self.env["project.project"].create({"name": "Pas suivi", "company_id": self.company.id})
        other_root = Folder.search([("scope", "=", "company"), ("parent_id", "=", False),
                                    ("company_id", "=", self.other_company.id)]) or Folder.create(
            {"scope": "company", "company_id": self.other_company.id})
        roots = [self.root_a, self.root_b, self.croot, other_root,
                 Folder.create({"scope": "department", "department_id": dept_in.id}),
                 Folder.create({"scope": "department", "department_id": dept_out.id}),
                 Folder.create({"scope": "project", "project_id": proj_in.id}),
                 Folder.create({"scope": "project", "project_id": proj_out.id})]
        Mark = self.env["bf.bookmark"].sudo()
        Usage = self.env["bf.bookmark.usage"].sudo()
        for user in (self.a, self.mgr):
            for i, root in enumerate(roots):
                mark = Mark.create({"name": f"M{i}", "url": f"https://m{i}.example/", "folder_id": root.id})
                line = Usage.create({"user_id": user.id, "bookmark_id": mark.id, "favorite": True})
                self.env.invalidate_all()
                with self.subTest(user=user.login, scope=root.scope, root=root.display_name):
                    self.assertEqual(
                        bool(Usage.with_user(user).search([("id", "=", line.id)])),
                        mark.with_user(user).has_access("read"))

    # ------------------------------------------------------------------
    # Le nom affiché d'une fiche qu'on ne peut pas lire ne dit rien
    # ------------------------------------------------------------------

    def _nested_folder_name(self, values=None, ctx=None):
        spec = {"usage_ids": {"fields": {"folder_id": {"fields": {"display_name": {}}}}}}
        r = self.env["bf.bookmark"].with_user(self.a).with_context(**(ctx or {})).onchange(
            values or {}, [], spec)
        return str(r.get("value"))

    def test_nested_lines_do_not_name_unreadable_folders(self):
        """🔴 Une ligne d'usage créée DANS le formulaire d'un signet, avec le dossier
        d'autrui en `folder_id` : le garde ne descend pas dans les sous-lignes, et Odoo
        calcule le nom du many2one en sudo. Par les valeurs, le contexte, `ir.default`."""
        line = [[0, "v1", {"folder_id": self.folder_b.id}]]
        self.assertNotIn("Dossier secret de B", self._nested_folder_name(values={"usage_ids": line}))
        self.assertNotIn("Dossier secret de B", self._nested_folder_name(ctx={"default_usage_ids": line}))
        self.env["ir.default"].with_user(self.a).set("bf.bookmark", "usage_ids", line, user_id=True)
        self.assertNotIn("Dossier secret de B", self._nested_folder_name())

    def test_proposal_parties_do_not_name_what_they_cannot_read(self):
        """Le décideur ne lit pas le titre du perso d'origine du proposant ; le proposant
        ne lit plus le signet accepté une fois déplacé hors de sa portée et renommé."""
        origin = self.env["bf.bookmark"].with_user(self.a).create(
            {"name": "Titre perso de A", "url": "https://perso-a.example/", "folder_id": self.root_a.id})
        prop = self.env["bf.bookmark.proposal"].with_user(self.a).create(
            {"name": "Doc", "url": "https://doc.example/", "target_folder_id": self.croot.id,
             "bookmark_id": origin.id})
        spec = {"bookmark_id": {"fields": {"display_name": {}}},
                "result_bookmark_id": {"fields": {"display_name": {}}}}
        seen = str(prop.with_user(self.mgr).web_read(spec))
        self.assertNotIn("Titre perso de A", seen)
        prop.with_user(self.mgr).action_accept()
        root_mgr = self.env["bf.bookmark.folder"].with_user(self.mgr)._personal_root()
        prop.result_bookmark_id.with_user(self.mgr).write({"folder_id": root_mgr.id})
        prop.result_bookmark_id.with_user(self.mgr).write({"name": "PERSO-DU-RESPONSABLE"})
        self.env.invalidate_all()
        self.assertNotIn("PERSO-DU-RESPONSABLE", str(prop.with_user(self.a).web_read(spec)))

    def test_readable_names_and_scheduled_tasks_are_untouched(self):
        """Contre-épreuve : ce qu'on peut lire garde son nom, et le superutilisateur (les
        tâches planifiées) voit le vrai nom de tout."""
        self.mark_a.with_user(self.a).action_toggle_favorite()
        line = self.env["bf.bookmark.usage"].with_user(self.a).search([("bookmark_id", "=", self.mark_a.id)])
        self.assertEqual(line.web_read({"bookmark_id": {"fields": {"display_name": {}}}})[0]
                         ["bookmark_id"]["display_name"], "Hameçon")
        self.assertEqual(self.env["bf.bookmark"].sudo().browse(self.secret_b.id).display_name,
                         "Titre secret de B")
        self.assertEqual(self.env["bf.bookmark.folder"].sudo().browse(self.folder_b.id).display_name,
                         "Dossier secret de B")
