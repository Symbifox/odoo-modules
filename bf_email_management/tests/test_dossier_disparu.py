"""Un dossier renommé ou supprimé sur le serveur (tâche #24976).

Rien n'est perdu : le corps, l'objet et les pièces jointes vivent dans cette
base, pas sur le serveur. Ce qui casse, c'est le **pointeur** — `imap_folder`
n'est jamais réconcilié avec ce que le serveur contient réellement. Deux
conséquences, éprouvées ici :

* une remise en boîte ne trouve plus rien à ramener, et laissait la ligne
  hors de TOUTE liste de travail — ni « Traités » qu'elle vient de quitter,
  ni « Boîte de réception » où `imap_in_inbox` faux l'empêchait d'entrer ;
* les lignes qui citent le dossier disparu n'avaient plus aucun nœud dans
  l'arbre des dossiers IMAP.
"""
import os
import re

from unittest.mock import patch

from odoo.tests import tagged

from .common import MobileApiCase
from .test_imap_folders_and_uid_guard import FakeImap

IMAP = "odoo.addons.bf_email_management.models.bf_email_imap"


class DossierDisparuCase(MobileApiCase):

    def setUp(self):
        super().setUp()
        self.account.writeback_archive = True
        self.archivee = self.inbound
        self.archivee.write({
            "is_handled": True,
            "imap_folder": "Archives/2026",
            "imap_uid": "500",
            "imap_in_inbox": False,
        })

    def _folders(self, key):
        return {f["key"]: f for f in self.as_owner().inbox_get_folders()}

    def _in_folder(self, key):
        page = self.as_owner().inbox_get_messages(key, 0, 200)
        return self.archivee.id in [m["id"] for m in page["messages"]]


@tagged("post_install", "-at_install")
class TestRemiseEnBoiteSansDossier(DossierDisparuCase):

    def test_the_content_never_depends_on_the_server(self):
        # Le point rassurant, et il mérite d'être écrit : supprimer le
        # dossier côté serveur ne touche à rien ici.
        self.assertTrue(self.archivee.body_html)
        self.assertTrue(self.archivee.subject)
        self.assertTrue(self._in_folder("all"))

    def test_a_vanished_folder_marks_the_link_unknown(self):
        fake = FakeImap(unselectable={"Archives/2026"})
        with patch(f"{IMAP}.open_connection", return_value=fake):
            self.archivee.action_unhandle()
        self.archivee.invalidate_recordset()
        self.assertFalse(self.archivee.imap_folder)
        self.assertFalse(self.archivee.imap_uid)
        self.assertFalse(
            self.archivee.imap_in_inbox,
            "on ne ment pas dans l'autre sens : le message n'est pas en INBOX",
        )

    def test_an_emptied_folder_marks_the_link_unknown_too(self):
        # Le dossier existe encore, le message n'y est plus.
        fake = FakeImap(mailboxes={"Archives/2026": {}})
        with patch(f"{IMAP}.open_connection", return_value=fake):
            self.archivee.action_unhandle()
        self.archivee.invalidate_recordset()
        self.assertFalse(self.archivee.imap_folder)

    def test_the_row_lands_back_in_the_inbox(self):
        # Le défaut : elle quittait « Traités » et n'arrivait nulle part.
        fake = FakeImap(unselectable={"Archives/2026"})
        with patch(f"{IMAP}.open_connection", return_value=fake):
            self.archivee.action_unhandle()
        self.archivee.invalidate_recordset()
        self.assertFalse(self._in_folder("handled"))
        self.assertTrue(
            self._in_folder("inbox"),
            "une remise en boîte doit remettre en boîte, même quand la copie "
            "serveur est introuvable",
        )

    def test_a_successful_restore_still_behaves_as_before(self):
        # Pas de régression : quand la copie EST là, elle revient vraiment.
        fake = FakeImap(mailboxes={
            "Archives/2026": {"500": self.archivee.message_id_header}})
        with patch(f"{IMAP}.open_connection", return_value=fake):
            self.archivee.action_unhandle()
        self.archivee.invalidate_recordset()
        self.assertEqual(self.archivee.imap_folder, "INBOX")
        self.assertTrue(self.archivee.imap_in_inbox)
        self.assertTrue(self.archivee.imap_uid)
        self.assertTrue(self._in_folder("inbox"))

    def test_a_refused_copy_does_not_forget_a_valid_location(self):
        # Le message est bien dans son dossier, seul le COPY est refusé :
        # le pointeur reste juste, il ne faut pas l'effacer.
        fake = FakeImap(
            mailboxes={"Archives/2026": {"500": self.archivee.message_id_header}},
            fail_copy=True)
        with patch(f"{IMAP}.open_connection", return_value=fake):
            self.archivee.action_unhandle()
        self.archivee.invalidate_recordset()
        self.assertEqual(self.archivee.imap_folder, "Archives/2026")


@tagged("post_install", "-at_install")
class TestArbreApresRenommage(DossierDisparuCase):

    def _cache(self, *names):
        import json
        self.account.write({
            "folder_cache": json.dumps([
                {"name": n, "delimiter": "/", "has_children": False,
                 "noselect": False} for n in names]),
            "folder_cache_date": "2999-01-01 00:00:00",
        })

    def test_a_renamed_folder_leaves_its_rows_reachable(self):
        self._cache("INBOX", "Archives/2026-classe")
        tree = self._folders(None)
        vanished = "imapf:%s:Archives/2026" % self.account.id
        self.assertIn(vanished, tree, "les lignes doivent garder un nœud")
        self.assertEqual(tree[vanished]["count"], 1)
        self.assertEqual(tree[vanished]["icon"], "fa-chain-broken")
        self.assertIn("absent du serveur", tree[vanished]["title"])

    def test_the_new_folder_shows_up_empty(self):
        self._cache("INBOX", "Archives/2026-classe")
        tree = self._folders(None)
        neuf = "imapf:%s:Archives/2026-classe" % self.account.id
        self.assertIn(neuf, tree)
        self.assertEqual(tree[neuf]["count"], 0)

    def test_a_folder_still_on_the_server_is_not_marked_broken(self):
        self._cache("INBOX", "Archives/2026")
        tree = self._folders(None)
        key = "imapf:%s:Archives/2026" % self.account.id
        self.assertNotEqual(tree[key]["icon"], "fa-chain-broken")
        self.assertEqual(tree[key]["title"], "Archives/2026")

    def test_opening_a_vanished_folder_still_lists_its_rows(self):
        self._cache("INBOX")
        self.assertTrue(self._in_folder(
            "imapf:%s:Archives/2026" % self.account.id))

    def test_a_colleague_vanished_folder_is_not_mine(self):
        self.foreign.sudo().write({"account_id": self.account.id,
                                   "imap_folder": "Boîte du voisin"})
        self._cache("INBOX")
        tree = self._folders(None)
        self.assertNotIn(
            "imapf:%s:Boîte du voisin" % self.account.id, tree,
            "les orphelins se lisent dans MES lignes, pas dans celles des autres",
        )


@tagged("post_install", "-at_install")
class TestLesSixDefinitionsSaccordent(MobileApiCase):
    """« Boîte de réception » veut dire la même chose partout.

    La définition vivait en six exemplaires, chacun portant un commentaire
    priant les cinq autres de rester d'accord. Ces tests remplacent la prière
    par un mécanisme.
    """

    def setUp(self):
        super().setUp()
        # Une ligne dont l'emplacement serveur est inconnu : le cas neuf,
        # celui qu'aucun des six exemplaires ne connaissait.
        self.orpheline = self.inbound
        self.orpheline.write({"is_handled": False, "imap_in_inbox": False,
                              "imap_folder": False, "imap_uid": False})

    def test_the_tree_counts_it(self):
        folders = {f["key"]: f for f in self.as_owner().inbox_get_folders()}
        self.assertTrue(folders["inbox"]["count"] >= 1)
        page = self.as_owner().inbox_get_messages("inbox", 0, 200)
        self.assertIn(self.orpheline.id, [m["id"] for m in page["messages"]])

    def test_the_dashboard_action_counts_it(self):
        action = self.env["bf.email.dashboard"].with_user(
            self.owner).action_view_inbox_active()
        found = self.env["bf.email"].with_user(self.owner).search(
            action["domain"])
        self.assertIn(self.orpheline.id, found.ids)

    def test_the_mobile_filter_and_the_python_domain_agree(self):
        """Les deux transcriptions comparées sur les MÊMES lignes.

        Le filtre mobile est du SQL écrit à la main, le domaine de l'arbre est
        une liste Odoo : les comparer sur leur texte ne prouverait rien. On
        les fait donc répondre sur la même population.
        """
        BfEmail = self.env["bf.email"].with_user(self.owner)
        par_le_domaine = set(BfEmail.search(
            BfEmail._inbox_domain() + [("user_id", "=", self.owner.id)]).ids)

        where, params = BfEmail._mobile_filter_sql("inbox")
        self.env.flush_all()
        self.env.cr.execute(
            "SELECT id FROM bf_email WHERE user_id = %%s AND active = true "
            "AND %s" % where, [self.owner.id] + list(params))
        par_le_sql = {r[0] for r in self.env.cr.fetchall()}

        self.assertEqual(
            par_le_domaine, par_le_sql,
            "l'arbre et le téléphone ne comptent pas la même boîte",
        )
        self.assertIn(self.orpheline.id, par_le_sql)

    def test_the_list_view_filter_counts_it(self):
        view = self.env.ref("bf_email_management.bf_email_view_search")
        self.assertIn("('imap_folder', '=', False)", view.arch_db or "")

    def test_the_window_action_counts_it(self):
        action = self.env.ref("bf_email_management.bf_email_action")
        self.assertIn("('imap_folder', '=', False)", action.domain or "")

    def test_the_systray_javascript_carries_every_leaf(self):
        # Le badge ne peut pas importer le domaine Python — il compte avant
        # qu'aucune action ne soit ouverte. Sa copie est donc épinglée ici.
        from odoo.modules.module import get_module_path
        module_path = get_module_path("bf_email_systray", display_warning=False)
        if not module_path:
            self.skipTest(
                "bf_email_systray absent du chemin d'addons : le badge n'est "
                "pas épinglé sur cette base")
        path = os.path.join(module_path, "static", "src", "js",
                            "bf_email_systray.js")
        source = open(path, encoding="utf-8").read()
        blob = re.sub(r"\s+", "", source)
        for leaf in ('["is_handled","=",false]',
                     '["imap_in_inbox","=",true]',
                     '["source","in",["chatter","gateway"]]',
                     '["imap_folder","=",false]',
                     '["user_id","=",user.userId]'):
            self.assertIn(
                leaf, blob,
                "le badge systray a divergé de bf.email._inbox_domain() : "
                "il lui manque %s" % leaf)
