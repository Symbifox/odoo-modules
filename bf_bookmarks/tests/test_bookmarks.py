# -*- coding: utf-8 -*-
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, tagged

NETSCAPE = """<!DOCTYPE NETSCAPE-Bookmark-file-1>
<META HTTP-EQUIV="Content-Type" CONTENT="text/html; charset=UTF-8">
<TITLE>Bookmarks</TITLE>
<H1>Bookmarks</H1>
<DL><p>
    <DT><A HREF="https://racine.example/">À la racine</A>
    <DT><H3>Workdesk</H3>
    <DL><p>
        <DT><A HREF="https://odoo.example/web">Odoo</A>
        <DT><H3>Outils</H3>
        <DL><p>
            <DT><A HREF="https://outil.example/">Outil &amp; cie</A>
        </DL><p>
        <DT><A HREF="javascript:alert(1)">Piège</A>
    </DL><p>
    <DT><A HREF="https://apres.example/">Après le dossier</A>
</DL><p>
"""


@tagged("post_install", "-at_install")
class TestBookmarks(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Une société à eux : une base réelle (ou semée pour le parcours) a déjà une
        # racine d'entreprise pour env.company, et la règle « une racine par cible »
        # refuserait celle des essais.
        cls.company = cls.env["res.company"].create({"name": "Société d'essai des signets"})
        cls.other_company = cls.env["res.company"].create({"name": "Autre société"})
        Users = cls.env["res.users"].with_context(no_reset_password=True)
        user_group = cls.env.ref("base.group_user")

        # Des identifiants qui ne peuvent pas croiser un vrai compte de la base.
        def make(login, groups=()):
            return Users.create({
                "name": login.capitalize(), "login": f"signets_essai_{login}",
                "email": f"{login}@example.com",
                "company_id": cls.company.id, "company_ids": [(6, 0, [cls.company.id])],
                "groups_id": [(6, 0, [user_group.id, *[g.id for g in groups]])],
            })

        cls.manager = make("responsable", [cls.env.ref("bf_bookmarks.group_bookmark_manager")])
        cls.chief = make("chef")
        cls.member = make("membre")
        cls.outsider = make("externe")
        cls.pm = make("charge")
        cls.stranger = Users.create({
            "name": "Ailleurs", "login": "signets_essai_ailleurs", "email": "ailleurs@example.com",
            "company_id": cls.other_company.id, "company_ids": [(6, 0, [cls.other_company.id])],
            "groups_id": [(6, 0, [user_group.id])],
        })

        Employee = cls.env["hr.employee"].with_company(cls.company)
        cls.department = cls.env["hr.department"].create(
            {"name": "Infogérance", "company_id": cls.company.id})
        chief_emp = Employee.create({"name": "Chef", "user_id": cls.chief.id,
                                     "department_id": cls.department.id})
        cls.department.manager_id = chief_emp
        Employee.create({"name": "Membre", "user_id": cls.member.id,
                         "department_id": cls.department.id})
        Employee.create({"name": "Externe", "user_id": cls.outsider.id})

        cls.project = cls.env["project.project"].create({
            "name": "Portail client", "user_id": cls.pm.id, "privacy_visibility": "employees",
            "company_id": cls.company.id})
        cls.project.message_subscribe(partner_ids=[cls.member.partner_id.id])

        Folder = cls.env["bf.bookmark.folder"]
        # La société des essais peut déjà avoir sa racine (créée à la volée ou par
        # l'installation) : on la reprend et on retire son premier signet, que les
        # essais d'ordre ne connaissent pas.
        cls.company_root = Folder.search([("scope", "=", "company"), ("parent_id", "=", False),
                                          ("company_id", "=", cls.company.id)])
        cls.company_root.bookmark_ids.unlink()
        # Sans racine automatique, c'est l'essai dédié qui doit rougir, pas la
        # préparation commune (sinon aucun essai ne tourne).
        cls.company_root = cls.company_root or Folder.create(
            {"scope": "company", "company_id": cls.company.id})
        cls.dept_root = Folder.create({"scope": "department", "department_id": cls.department.id})
        cls.project_root = Folder.create({"scope": "project", "project_id": cls.project.id})
        Bookmark = cls.env["bf.bookmark"]
        cls.company_mark = Bookmark.create({
            "name": "Intranet", "url": "https://intranet.example/", "folder_id": cls.company_root.id})
        cls.dept_mark = Bookmark.create({
            "name": "Supervision", "url": "https://supervision.example/", "folder_id": cls.dept_root.id})
        cls.project_mark = Bookmark.create({
            "name": "Portail", "url": "https://portail.example/", "folder_id": cls.project_root.id})

    def _visible(self, user, record):
        return bool(record.with_user(user).search([("id", "=", record.id)]))

    def _personal_mark(self, user, name="Perso", url="https://perso.example/"):
        root = self.env["bf.bookmark.folder"].with_user(user)._personal_root()
        return self.env["bf.bookmark"].with_user(user).create(
            {"name": name, "url": url, "folder_id": root.id})

    # ------------------------------------------------------------------
    # Qui voit quoi
    # ------------------------------------------------------------------

    def test_personal_is_private(self):
        mark = self._personal_mark(self.member)
        self.assertTrue(self._visible(self.member, mark))
        self.assertFalse(self._visible(self.outsider, mark))
        # 🔴 Le responsable des signets ne voit pas le perso des autres.
        self.assertFalse(self._visible(self.manager, mark))
        with self.assertRaises(AccessError):
            mark.with_user(self.outsider).write({"name": "Volé"})

    def test_manager_cannot_see_others_personal_folders(self):
        # 🔴 Les signets ET les dossiers : un dossier perso visible révèle déjà ses
        # titres, et l'extension du responsable en ferait une section.
        root = self.env["bf.bookmark.folder"].with_user(self.member)._personal_root()
        self.assertFalse(self._visible(self.manager, root))
        self.assertFalse(self._visible(self.outsider, root))
        tree = self.env["bf.bookmark.folder"].with_user(self.manager).extension_tree()
        personal = [s for s in tree["sections"] if s["scope"] == "personal"]
        self.assertEqual([s["id"] for s in personal], [tree["personal_root"]])

    def test_company_visible_to_company_staff_only(self):
        for user in (self.member, self.outsider, self.chief, self.pm, self.manager):
            self.assertTrue(self._visible(user, self.company_mark), user.login)
        self.assertFalse(self._visible(self.stranger, self.company_mark))

    def test_department_visible_to_members_and_manager(self):
        self.assertTrue(self._visible(self.member, self.dept_mark))
        self.assertTrue(self._visible(self.chief, self.dept_mark))
        self.assertFalse(self._visible(self.outsider, self.dept_mark))
        self.assertFalse(self._visible(self.pm, self.dept_mark))
        self.assertTrue(self._visible(self.manager, self.dept_mark))

    def test_project_visible_to_followers_and_project_manager(self):
        self.assertTrue(self._visible(self.member, self.project_mark))
        self.assertTrue(self._visible(self.pm, self.project_mark))
        self.assertFalse(self._visible(self.outsider, self.project_mark))
        self.assertFalse(self._visible(self.chief, self.project_mark))

    # ------------------------------------------------------------------
    # Qui modifie quoi
    # ------------------------------------------------------------------

    def test_only_manager_writes_company(self):
        with self.assertRaises(AccessError):
            self.company_mark.with_user(self.member).write({"name": "Changé"})
        with self.assertRaises(AccessError):
            self.env["bf.bookmark"].with_user(self.member).create({
                "name": "Ajout", "url": "https://x.example/", "folder_id": self.company_root.id})
        self.company_mark.with_user(self.manager).write({"name": "Intranet 2"})
        self.assertEqual(self.company_mark.name, "Intranet 2")

    def test_department_manager_writes_department(self):
        self.dept_mark.with_user(self.chief).write({"name": "Supervision 2"})
        with self.assertRaises(AccessError):
            self.dept_mark.with_user(self.member).write({"name": "Non"})

    def test_project_manager_writes_project(self):
        self.project_mark.with_user(self.pm).write({"name": "Portail 2"})
        with self.assertRaises(AccessError):
            self.project_mark.with_user(self.member).write({"name": "Non"})

    # ------------------------------------------------------------------
    # 🔴 L'escalade par déplacement
    # ------------------------------------------------------------------

    def test_cannot_turn_personal_folder_into_company(self):
        # Sans racine d'entreprise, la contrainte d'unicité ne masque rien : seul le
        # contrôle d'après l'écriture peut refuser.
        self.company_root.unlink()
        root = self.env["bf.bookmark.folder"].with_user(self.member)._personal_root()
        with self.assertRaises(AccessError):
            root.write({"scope": "company", "company_id": self.company.id})

    def test_cannot_turn_personal_folder_into_team(self):
        other = self.env["hr.department"].create({"name": "Direction"})
        root = self.env["bf.bookmark.folder"].with_user(self.member)._personal_root()
        with self.assertRaises(AccessError):
            root.write({"scope": "department", "department_id": other.id})

    def test_cannot_move_personal_folder_under_company(self):
        Folder = self.env["bf.bookmark.folder"].with_user(self.member)
        root = Folder._personal_root()
        sub = Folder.create({"name": "Mes liens", "parent_id": root.id})
        with self.assertRaises(AccessError):
            sub.write({"parent_id": self.company_root.id})

    def test_cannot_move_personal_bookmark_into_company(self):
        mark = self._personal_mark(self.member)
        with self.assertRaises(AccessError):
            mark.write({"folder_id": self.company_root.id})

    def test_cannot_create_root_for_someone_else(self):
        with self.assertRaises(AccessError):
            self.env["bf.bookmark.folder"].with_user(self.member).create(
                {"scope": "personal", "owner_id": self.outsider.id})

    # ------------------------------------------------------------------
    # La cible suit l'arbre
    # ------------------------------------------------------------------

    def test_children_inherit_and_follow_root_target(self):
        Folder = self.env["bf.bookmark.folder"]
        root = self.dept_root
        child = Folder.create({"name": "Sous", "parent_id": root.id, "scope": "personal"})
        grandchild = Folder.create({"name": "Sous-sous", "parent_id": child.id})
        mark = self.env["bf.bookmark"].create(
            {"name": "Profond", "url": "https://profond.example/", "folder_id": grandchild.id})
        self.assertEqual(child.scope, "department")
        self.assertEqual(grandchild.department_id, self.department)
        self.assertEqual(mark.department_id, self.department)
        # Changer la cible de la racine emporte toute la descendance.
        other = self.env["hr.department"].create({"name": "Direction"})
        root.write({"department_id": other.id})
        self.assertEqual(grandchild.department_id, other)
        self.assertEqual(mark.department_id, other)
        # Et un sous-dossier déplacé sous une autre racine en prend la cible.
        child.write({"parent_id": self.company_root.id})
        self.assertEqual(grandchild.scope, "company")
        self.assertEqual(mark.scope, "company")
        self.assertFalse(mark.department_id)

    def test_a_root_keeps_only_its_own_target(self):
        Folder = self.env["bf.bookmark.folder"]
        other = self.env["hr.department"].create({"name": "Direction"})
        root = Folder.create({"scope": "department", "department_id": other.id,
                              "project_id": self.project.id, "owner_id": self.member.id})
        self.assertFalse(root.project_id)
        self.assertFalse(root.owner_id)
        mine = Folder.with_user(self.member)._personal_root()
        self.assertFalse(mine.company_id)
        # Une racine perso qui porterait aussi un projet serait vue par ses abonnés.
        project2 = self.env["project.project"].create({"name": "Autre"})
        perso = Folder.with_user(self.outsider).create(
            {"scope": "personal", "project_id": project2.id})
        self.assertFalse(perso.project_id)

    def test_single_root_per_target(self):
        with self.assertRaises(ValidationError):
            self.env["bf.bookmark.folder"].create(
                {"scope": "company", "company_id": self.company.id})

    def test_root_named_after_its_target(self):
        self.assertEqual(self.dept_root.name, "Infogérance")
        self.assertEqual(self.project_root.name, "Portail client")
        self.assertEqual(self.company_root.name, self.company.name)
        self.assertEqual(self.dept_root.company_id, self.department.company_id)

    def test_folder_cycle_refused(self):
        Folder = self.env["bf.bookmark.folder"]
        a = Folder.create({"name": "A", "parent_id": self.company_root.id})
        b = Folder.create({"name": "B", "parent_id": a.id})
        # Odoo lève « Recursion Detected » (UserError) avant notre contrainte, et
        # ValidationError en hérite : les deux gardes comptent.
        with self.assertRaises(UserError):
            a.write({"parent_id": b.id})

    # ------------------------------------------------------------------
    # Les adresses
    # ------------------------------------------------------------------

    def test_dangerous_schemes_refused(self):
        for url in ("javascript:alert(1)", "JavaScript:alert(1)", "data:text/html,x",
                    "  javascript:alert(1)", "vbscript:x", "https://", "sans-schema.example",
                    "file://hote-attaquant/partage", "file:///etc/passwd", "ftp://c.example/x",
                    "java\tscript:alert(1)", "\x00javascript:alert(1)",
                    # A un « hôte » : seule la liste blanche des schémas l'arrête.
                    "javascript://x/%0aalert(1)", "data://x/text/html,y"):
            with self.assertRaises(ValidationError, msg=url):
                self.env["bf.bookmark"].create(
                    {"name": "x", "url": url, "folder_id": self.company_root.id})
        with self.assertRaises(ValidationError):
            self.company_mark.write({"url": "javascript:alert(1)"})

    def test_accepted_schemes(self):
        for url in ("https://a.example/", "http://b.example", "HTTPS://c.example/x"):
            mark = self.env["bf.bookmark"].create(
                {"name": "x", "url": url, "folder_id": self.company_root.id})
            self.assertEqual(mark.url, url)

    # ------------------------------------------------------------------
    # L'extension
    # ------------------------------------------------------------------

    def test_extension_tree_sections_and_rights(self):
        tree = self.env["bf.bookmark.folder"].with_user(self.member).extension_tree()
        scopes = [s["scope"] for s in tree["sections"]]
        self.assertEqual(scopes, ["company", "department", "project", "personal"])
        writable = {s["scope"]: s["writable"] for s in tree["sections"]}
        self.assertEqual(writable, {"company": False, "department": False,
                                    "project": False, "personal": True})
        company = tree["sections"][0]
        self.assertEqual(company["children"][0]["url"], "https://intranet.example/")
        self.assertEqual(tree["personal_root"], tree["sections"][-1]["id"])

        chief_tree = self.env["bf.bookmark.folder"].with_user(self.chief).extension_tree()
        chief = {s["scope"]: s["writable"] for s in chief_tree["sections"]}
        self.assertEqual(chief, {"company": False, "department": True, "personal": True})

        outsider = self.env["bf.bookmark.folder"].with_user(self.outsider).extension_tree()
        self.assertEqual([s["scope"] for s in outsider["sections"]], ["company", "personal"])

    def test_extension_tree_hash_moves_with_content(self):
        Folder = self.env["bf.bookmark.folder"].with_user(self.member)
        first = Folder.extension_tree()["hash"]
        self.assertEqual(first, Folder.extension_tree()["hash"])
        self._personal_mark(self.member)
        self.assertNotEqual(first, Folder.extension_tree()["hash"])

    def test_extension_tree_order_mixes_folders_and_bookmarks(self):
        Folder = self.env["bf.bookmark.folder"]
        Folder.create({"name": "Dossier", "parent_id": self.company_root.id, "sequence": 5})
        self.env["bf.bookmark"].create({"name": "Premier", "url": "https://1.example/",
                                        "folder_id": self.company_root.id, "sequence": 1})
        tree = Folder.with_user(self.member).extension_tree()
        titles = [c["title"] for c in tree["sections"][0]["children"]]
        self.assertEqual(titles, ["Premier", "Dossier", "Intranet"])

    def test_extension_apply_personal_roundtrip(self):
        Folder = self.env["bf.bookmark.folder"].with_user(self.member)
        root = Folder.extension_tree()["personal_root"]
        result = Folder.extension_apply([
            {"op": "create", "kind": "folder", "tmp": "t1", "parent": root, "title": "Clients"},
            {"op": "create", "kind": "bookmark", "tmp": "t2", "parent": "t1",
             "title": "Client A", "url": "https://client-a.example/"},
            {"op": "create", "kind": "bookmark", "tmp": "t3", "parent": "t1",
             "title": "Client B", "url": "https://client-b.example/", "index": 0},
            {"op": "update", "id": "t2", "title": "Client A (portail)"},
        ])
        self.assertEqual(result["errors"], [])
        folder = self.env["bf.bookmark.folder"].browse(int(result["created"]["t1"][1:]))
        self.assertEqual(folder.owner_id, self.member)
        titles = [c["title"] for c in
                  next(c for c in Folder.extension_tree()["sections"][-1]["children"]
                       if c["id"] == result["created"]["t1"])["children"]]
        self.assertEqual(titles, ["Client B", "Client A (portail)"])
        # Déplacer puis supprimer.
        client_a = result["created"]["t2"]
        dossier_t1 = result["created"]["t1"]
        result = Folder.extension_apply([
            {"op": "move", "id": client_a, "parent": root, "index": 0},
            {"op": "delete", "id": dossier_t1},
        ])
        self.assertEqual(result["errors"], [])
        # Archivé, pas effacé : une passe fautive se rattrape.
        self.assertTrue(folder.exists())
        self.assertFalse(folder.active)
        self.assertNotIn(f"'{dossier_t1}'", str(Folder.extension_tree()))
        self.assertTrue(self.env["bf.bookmark"].browse(int(client_a[1:])).exists())

    def test_extension_apply_refusal_does_not_block_the_rest(self):
        Folder = self.env["bf.bookmark.folder"].with_user(self.member)
        root = Folder.extension_tree()["personal_root"]
        result = Folder.extension_apply([
            {"op": "create", "kind": "bookmark", "parent": f"f{self.company_root.id}",
             "title": "Intrus", "url": "https://intrus.example/"},
            {"op": "update", "id": f"b{self.company_mark.id}", "title": "Piraté"},
            {"op": "delete", "id": f"b{self.company_mark.id}"},
            {"op": "move", "id": f"b{self.company_mark.id}", "parent": root},
            {"op": "create", "kind": "bookmark", "parent": root,
             "title": "Piège", "url": "javascript:alert(1)"},
            {"op": "delete", "id": root},
            {"op": "delete", "id": "x12"},
            {"op": "create", "kind": "bookmark", "tmp": "ok", "parent": root,
             "title": "Légitime", "url": "https://ok.example/"},
        ])
        self.assertEqual([e["index"] for e in result["errors"]], [0, 1, 2, 3, 4, 5, 6])
        self.assertIn("ok", result["created"])
        self.assertEqual(self.company_mark.name, "Intranet")
        self.assertEqual(self.company_mark.folder_id, self.company_root)
        self.assertFalse(self.env["bf.bookmark"].search([("name", "=", "Intrus")]))

    def test_extension_apply_cannot_reach_someone_elses_personal(self):
        mark = self._personal_mark(self.outsider)
        result = self.env["bf.bookmark.folder"].with_user(self.member).extension_apply([
            {"op": "update", "id": f"b{mark.id}", "title": "Lu"},
            {"op": "delete", "id": f"b{mark.id}"},
        ])
        self.assertEqual(len(result["errors"]), 2)
        self.assertEqual(mark.name, "Perso")

    def test_extension_apply_manager_edits_company_from_browser(self):
        result = self.env["bf.bookmark.folder"].with_user(self.manager).extension_apply([
            {"op": "update", "id": f"b{self.company_mark.id}", "url": "https://intranet2.example/"},
        ])
        self.assertEqual(result["errors"], [])
        self.assertEqual(self.company_mark.url, "https://intranet2.example/")

    # ------------------------------------------------------------------
    # L'import
    # ------------------------------------------------------------------

    def test_import_netscape_structure(self):
        Import = self.env["bf.bookmark.import"].with_user(self.member)
        root = self.env["bf.bookmark.folder"].with_user(self.member)._personal_root()
        stats = Import._import_html(NETSCAPE, root)
        self.assertEqual(stats, {"folders": 2, "bookmarks": 4, "skipped": 0, "refused": 1})
        self.assertEqual(root.bookmark_ids.mapped("name"), ["À la racine", "Après le dossier"])
        workdesk = root.child_ids
        self.assertEqual(workdesk.name, "Workdesk")
        self.assertEqual(workdesk.bookmark_ids.mapped("url"), ["https://odoo.example/web"])
        self.assertEqual(workdesk.child_ids.name, "Outils")
        self.assertEqual(workdesk.child_ids.bookmark_ids.name, "Outil & cie")
        self.assertEqual(workdesk.child_ids.owner_id, self.member)

    def test_import_replays_without_duplicates(self):
        Import = self.env["bf.bookmark.import"].with_user(self.member)
        root = self.env["bf.bookmark.folder"].with_user(self.member)._personal_root()
        Import._import_html(NETSCAPE, root)
        stats = Import._import_html(NETSCAPE, root)
        self.assertEqual(stats, {"folders": 0, "bookmarks": 0, "skipped": 4, "refused": 1})
        self.assertEqual(self.env["bf.bookmark"].search_count([("owner_id", "=", self.member.id)]), 4)

    def test_import_into_company_needs_the_right(self):
        with self.assertRaises(AccessError):
            self.env["bf.bookmark.import"].with_user(self.member)._import_html(
                NETSCAPE, self.company_root.with_user(self.member))

    # ------------------------------------------------------------------
    # Revue de sécurité adverse
    # ------------------------------------------------------------------

    def test_rpc_context_cannot_skip_the_after_write_check(self):
        """🔴 L'attaque exacte : un contexte fourni par l'appelant, comme par RPC."""
        Folder = self.env["bf.bookmark.folder"].with_user(self.member)
        sub = Folder.create({"name": "Hameçon", "parent_id": Folder._personal_root().id})
        self.env["bf.bookmark"].with_user(self.member).create(
            {"name": "Paie", "url": "https://paie-hameçon.example/", "folder_id": sub.id})
        for vals in (
            {"parent_id": self.company_root.id, "scope": "company",
             "company_id": self.company.id, "owner_id": False},
            {"parent_id": False, "scope": "project", "project_id": self.env["project.project"]
             .create({"name": "Sans racine", "company_id": self.company.id}).id},
        ):
            with self.assertRaises(AccessError):
                sub.with_context(bf_bookmark_propagating=True).write(vals)
        self.assertEqual(sub.scope, "personal")

    def test_cannot_plant_someone_elses_personal_root(self):
        Folder = self.env["bf.bookmark.folder"].with_user(self.member)
        sub = Folder.create({"name": "Cadeau", "parent_id": Folder._personal_root().id})
        with self.assertRaises(AccessError):
            sub.write({"parent_id": False, "scope": "personal", "owner_id": self.outsider.id})

    def test_depth_is_bounded(self):
        from odoo.addons.bf_bookmarks.models.bookmark_folder import MAX_DEPTH
        Folder = self.env["bf.bookmark.folder"]
        parent = self.company_root
        for level in range(MAX_DEPTH):
            parent = Folder.create({"name": f"N{level}", "parent_id": parent.id})
        with self.assertRaises(ValidationError):
            Folder.create({"name": "Trop", "parent_id": parent.id})

    def test_extension_apply_is_bounded(self):
        from odoo.addons.bf_bookmarks.models.bookmark_folder import MAX_OPS
        Folder = self.env["bf.bookmark.folder"].with_user(self.member)
        with self.assertRaises(UserError):
            Folder.extension_apply([{"op": "noop"}] * (MAX_OPS + 1))

    def test_missing_and_forbidden_look_the_same(self):
        mark = self._personal_mark(self.outsider, name="Secret du voisin")
        Folder = self.env["bf.bookmark.folder"].with_user(self.member)
        result = Folder.extension_apply([
            {"op": "delete", "id": f"b{mark.id}"},
            {"op": "delete", "id": "b999999999"},
            {"op": "update", "id": f"b{self.company_mark.id}", "title": "x"},
        ])
        messages = [e["message"] for e in result["errors"]]
        self.assertEqual(messages[0], messages[1])
        self.assertNotIn("Secret du voisin", str(result))
        self.assertNotIn("Intranet", str(result))

    def test_root_uniqueness_is_not_an_oracle(self):
        # Sans droit d'écrire sur le département, la réponse est un refus d'accès,
        # pas « une racine existe déjà pour … ».
        with self.assertRaises(AccessError):
            self.env["bf.bookmark.folder"].with_user(self.member).create(
                {"scope": "department", "department_id": self.department.id})

    def test_team_folder_always_has_a_company(self):
        project = self.env["project.project"].create({"name": "Sans société", "company_id": False})
        root = self.env["bf.bookmark.folder"].create({"scope": "project", "project_id": project.id})
        self.assertTrue(root.company_id)

    def test_import_is_bounded(self):
        from odoo.addons.bf_bookmarks.wizard.bookmark_import import MAX_BOOKMARKS
        big = "<DL>" + "".join(f'<DT><A HREF="https://x{i}.example/">x</A>'
                               for i in range(MAX_BOOKMARKS + 1)) + "</DL>"
        root = self.env["bf.bookmark.folder"].with_user(self.member)._personal_root()
        with self.assertRaises(UserError):
            self.env["bf.bookmark.import"].with_user(self.member)._import_html(big, root)

    # ------------------------------------------------------------------
    # Racines d'entreprise, boutons, restauration (18.0.1.1.0)
    # ------------------------------------------------------------------

    def test_new_company_gets_its_root_at_first_use(self):
        self.env["ir.config_parameter"].sudo().set_param("web.base.url", "https://odoo.example")
        company = self.env["res.company"].create({"name": "Filiale"})
        Folder = self.env["bf.bookmark.folder"]
        racine = [("scope", "=", "company"), ("parent_id", "=", False), ("company_id", "=", company.id)]
        self.assertFalse(Folder.search(racine), "pas de section vide à la création")
        employe = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Employé filiale", "login": "signets_essai_filiale",
            "company_id": company.id, "company_ids": [(6, 0, [company.id])],
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id])]})
        tree = Folder.with_user(employe).extension_tree()
        root = Folder.search(racine)
        self.assertEqual(len(root), 1)
        self.assertEqual(root.name, "Filiale")
        self.assertEqual(root.bookmark_ids.url, "https://odoo.example/odoo")
        self.assertEqual(tree["sections"][0]["id"], f"f{root.id}")

    def test_archive_companies_get_no_root(self):
        # Une société sans employé dont elle est la société principale (une société
        # d'archive) ne reçoit rien à l'installation ou à la montée.
        archive = self.env["res.company"].create({"name": "Ancienne"})
        self.env["bf.bookmark.folder"]._ensure_company_roots()
        self.assertFalse(self.env["bf.bookmark.folder"].search([("company_id", "=", archive.id),
                                                                ("scope", "=", "company")]))

    def test_ensure_company_roots_is_idempotent_and_never_touches_a_root(self):
        Folder = self.env["bf.bookmark.folder"]
        self.company_root.name = "Renommée à la main"
        self.assertFalse(Folder._ensure_company_roots(self.company))
        self.assertEqual(self.company_root.name, "Renommée à la main")
        self.company_root.active = False
        self.assertFalse(Folder._ensure_company_roots(self.company),
                         "une racine archivée n'est pas recréée à côté")

    def test_project_button_creates_root_for_the_project_manager(self):
        project = self.env["project.project"].create(
            {"name": "Neuf", "user_id": self.pm.id, "company_id": self.company.id})
        action = project.with_user(self.pm).action_open_bookmarks()
        root = self.env["bf.bookmark.folder"].search([("project_id", "=", project.id)])
        self.assertEqual(len(root), 1)
        self.assertEqual(action["context"]["default_folder_id"], root.id)
        self.assertEqual(action["domain"], [("project_id", "=", project.id)])

    def test_project_button_reads_only_for_a_member(self):
        project = self.env["project.project"].create(
            {"name": "Neuf 2", "user_id": self.pm.id, "company_id": self.company.id})
        project.message_subscribe(partner_ids=[self.member.partner_id.id])
        action = project.with_user(self.member).action_open_bookmarks()
        self.assertFalse(self.env["bf.bookmark.folder"].search([("project_id", "=", project.id)]))
        self.assertNotIn("default_folder_id", action["context"])
        # Racine existante, visible mais pas modifiable par lui : pas de dossier proposé.
        project.with_user(self.pm).action_open_bookmarks()
        action = project.with_user(self.member).action_open_bookmarks()
        self.assertNotIn("default_folder_id", action["context"])

    def test_counts_on_project_and_client(self):
        client = self.env["res.partner"].create({"name": "Client Z", "is_company": True})
        contact = self.env["res.partner"].create({"name": "Contact Z", "parent_id": client.id})
        self.project.partner_id = contact
        self.assertEqual(self.project.with_user(self.member).bookmark_count, 1)
        self.assertEqual(self.project.with_user(self.outsider).bookmark_count, 0,
                         "le compte du projet suit lui aussi celui qui regarde")
        self.assertEqual(client.with_user(self.member).bookmark_count, 1)
        self.assertEqual(client.with_user(self.outsider).bookmark_count, 0,
                         "le compte suit les droits : l'externe ne suit pas le projet")
        action = client.with_user(self.member).action_open_bookmarks()
        self.assertEqual(self.env["bf.bookmark"].with_user(self.member).search(action["domain"]),
                         self.project_mark)

    def test_counts_do_not_break_a_reader_without_bookmark_rights(self):
        portal = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Portail", "login": "signets_essai_portail",
            "groups_id": [(6, 0, [self.env.ref("base.group_portal").id])]})
        self.assertEqual(portal.partner_id.with_user(portal).bookmark_count, 0)

    def test_restore_what_the_browser_deleted(self):
        Folder = self.env["bf.bookmark.folder"].with_user(self.member)
        root = Folder._personal_root()
        folder = Folder.create({"name": "Clients", "parent_id": root.id})
        sub = Folder.create({"name": "Sous", "parent_id": folder.id})
        mark = self.env["bf.bookmark"].with_user(self.member).create(
            {"name": "Profond", "url": "https://profond.example/", "folder_id": sub.id})
        result = Folder.extension_apply([{"op": "delete", "id": f"f{folder.id}"}])
        self.assertEqual(result["errors"], [])
        self.assertTrue(mark.archived_from_browser and not mark.active)
        self.assertTrue(folder.archived_from_browser and folder.archived_date)
        # Restaurer le signet seul remet aussi ses dossiers, sinon il resterait invisible.
        mark.with_user(self.member).action_restore_from_browser()
        self.assertTrue(mark.active and sub.active and folder.active)
        self.assertFalse(mark.archived_from_browser or folder.archived_from_browser)
        tree = str(Folder.extension_tree())
        self.assertIn("Profond", tree)

    def test_restore_follows_write_rights(self):
        result = self.env["bf.bookmark.folder"].with_user(self.manager).extension_apply(
            [{"op": "delete", "id": f"b{self.company_mark.id}"}])
        self.assertEqual(result["errors"], [])
        with self.assertRaises(AccessError):
            self.company_mark.with_user(self.member).action_restore_from_browser()
        self.company_mark.with_user(self.manager).action_restore_from_browser()
        self.assertTrue(self.company_mark.active)

    # ------------------------------------------------------------------
    # Ajout depuis la barre (18.0.1.2.0)
    # ------------------------------------------------------------------

    def test_can_edit_is_searchable_and_follows_rights(self):
        Folder = self.env["bf.bookmark.folder"]
        sub = Folder.create({"name": "Outils", "parent_id": self.dept_root.id})
        member = Folder.with_user(self.member)
        root = member._personal_root()
        mine = member.create({"name": "Mien", "parent_id": root.id})
        editable = member.search([("can_edit", "=", True)])
        self.assertIn(root, editable)
        self.assertIn(mine, editable)
        self.assertNotIn(self.company_root, editable)
        self.assertNotIn(sub, editable, "un sous-dossier d'une section imposée n'est pas proposé")
        self.assertIn(self.company_root, member.search([("can_edit", "=", False)]))
        chief = Folder.with_user(self.chief).search([("can_edit", "=", True)])
        self.assertIn(sub, chief, "le gestionnaire du département écrit dans ses sous-dossiers")
        self.assertEqual(member.search([("can_edit", "!=", False)]), editable)

    def test_quick_add_form_saves_in_personal_root_by_default(self):
        from odoo.tests import Form
        view = self.env.ref("bf_bookmarks.bookmark_view_form_quick")
        Bookmark = self.env["bf.bookmark"].with_user(self.member).with_context(
            default_name="Tâche 42", default_url="https://odoo.example/odoo/project/1/tasks/42")
        with Form(Bookmark, view=view) as form:
            pass
        mark = form.record
        self.assertEqual(mark.folder_id, self.env["bf.bookmark.folder"].with_user(self.member)._personal_root())
        self.assertEqual(mark.url, "https://odoo.example/odoo/project/1/tasks/42")
