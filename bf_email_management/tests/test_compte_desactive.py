"""Un compte désactivé quitte les listes de travail.

Le cas d'origine : une personne retire un compte personnel de ses comptes
actifs, la relève s'arrête, mais plus d'un millier de lignes non traitées
qu'il avait apportées restent dans la boîte, « À répondre » et le badge. Et
« Traité » sur l'une d'elles allait encore ouvrir la boîte de ce compte pour
la ranger dans Archives.

Ce qu'on éprouve : les lignes sortent des listes de travail SANS être
modifiées, réactiver le compte les ramène, l'historique les garde, le courrier
sans compte n'est pas emporté, les quatre recopies du domaine disent la même
chose, et plus aucun geste n'ouvre de connexion vers un compte désactivé.
"""
import os
import re
from unittest.mock import patch

from odoo.tests import tagged

from .common import MobileApiCase
from ..models import bf_email_imap

IMAP = "odoo.addons.bf_email_management.models.bf_email_imap"

LEAF_XML = "('account_id', 'not any', [('active', '=', False)])"


@tagged("post_install", "-at_install")
class TestCompteDesactive(MobileApiCase):

    def setUp(self):
        super().setUp()
        self.wild = self.env["bf.email.account"].create({
            "name": "Boîte perso",
            "user_id": self.owner.id,
            "host": "imap.perso.invalid",
            "port": 993,
            "login": "owner@perso.invalid",
            "password": "x",
            "state": "connected",
            "writeback_archive": True,
        })
        BfEmail = self.as_owner()
        self.perso = BfEmail.create(dict(
            self._vals(subject="Relevé de carte", sender="banque@perso.test",
                       direction="in", status="new", root=False,
                       body="Votre relevé est prêt.", uid="701"),
            account_id=self.wild.id,
        ))
        # Courrier né dans Odoo : aucun compte, il ne doit jamais être emporté.
        self.chatter = BfEmail.create({
            "subject": "Note de chatter",
            "email_from": "client@acme.test",
            "direction": "in", "status": "new", "source": "chatter",
            "user_id": self.owner.id,
            "message_id_header": "<chatter-compte@test.invalid>",
            "date": "2026-08-20 12:00:00",
        })

    def _ids(self, folder):
        page = self.as_owner().inbox_get_messages(folder, 0, 500)
        return {m["id"] for m in page["messages"]}

    def _desactiver(self):
        self.wild.with_user(self.owner).write({"active": False})

    # ------------------------------------------------------------------
    # Les listes de travail
    # ------------------------------------------------------------------
    def test_an_active_account_is_in_the_inbox(self):
        # Point de départ, sinon les tests suivants ne prouvent rien.
        self.assertIn(self.perso.id, self._ids("inbox"))
        self.assertIn(self.perso.id, self._ids("unread"))
        self.assertIn(self.perso.id, self._ids("to_reply"))

    def test_deactivating_empties_every_working_list(self):
        self._desactiver()
        for folder in ("inbox", "unread", "to_reply", "unrouted"):
            self.assertNotIn(
                self.perso.id, self._ids(folder),
                "le courrier d'un compte désactivé reste dans « %s »" % folder)

    def test_a_snoozed_row_leaves_the_snoozed_list(self):
        self.perso.write({"is_handled": True,
                          "snoozed_until": "2999-01-01 00:00:00"})
        self.assertIn(self.perso.id, self._ids("snoozed"))
        self._desactiver()
        self.assertNotIn(self.perso.id, self._ids("snoozed"))

    def test_the_rows_themselves_are_untouched(self):
        self._desactiver()
        self.perso.invalidate_recordset()
        self.assertFalse(self.perso.is_handled)
        self.assertEqual(self.perso.status, "new")
        self.assertTrue(self.perso.active)

    def test_reactivating_brings_them_back(self):
        self._desactiver()
        self.wild.with_user(self.owner).write({"active": True})
        self.assertIn(self.perso.id, self._ids("inbox"))

    def test_history_keeps_them(self):
        self._desactiver()
        self.assertIn(self.perso.id, self._ids("all"))

    def test_mail_without_an_account_stays(self):
        self._desactiver()
        self.assertIn(self.chatter.id, self._ids("inbox"))
        # Et la boîte du compte resté actif n'est pas touchée non plus.
        self.assertIn(self.inbound.id, self._ids("inbox"))

    def test_the_tree_count_follows(self):
        avant = {f["key"]: f for f in self.as_owner().inbox_get_folders()}
        self._desactiver()
        apres = {f["key"]: f for f in self.as_owner().inbox_get_folders()}
        self.assertEqual(apres["inbox"]["count"], avant["inbox"]["count"] - 1)

    def test_the_dashboard_counts_follow(self):
        Dash = self.env["bf.email.dashboard"].with_user(self.owner)
        avant = Dash._get_actionable()
        self._desactiver()
        apres = Dash._get_actionable()
        self.assertEqual(apres["inbox_active"], avant["inbox_active"] - 1)
        self.assertEqual(apres["unrouted_orphans"],
                         avant["unrouted_orphans"] - 1)
        for name in ("action_view_awaiting_reply",
                     "action_view_unrouted_orphans",
                     "action_view_vip_pending"):
            domain = getattr(Dash, name)()["domain"]
            self.assertIn(("account_id", "not any", [("active", "=", False)]),
                          domain, name)

    # ------------------------------------------------------------------
    # Les recopies du domaine
    # ------------------------------------------------------------------
    def test_the_phone_agrees_with_the_tree(self):
        """Le SQL du téléphone et les domaines Python, sur les mêmes lignes."""
        self.perso.copy({"is_handled": True, "snoozed_until": "2999-01-01",
                         "message_id_header": "<reporte-compte@test.invalid>"})
        self._desactiver()
        BfEmail = self.as_owner()
        defs = {d["key"]: d for d in BfEmail._inbox_folder_defs()}
        self.env.flush_all()
        for folder in ("inbox", "unread", "unrouted", "snoozed"):
            par_le_domaine = set(BfEmail.search(
                [("user_id", "=", self.owner.id)]
                + defs[folder]["domain"]).ids)
            where, params = BfEmail._mobile_filter_sql(folder)
            self.env.cr.execute(
                "SELECT id FROM bf_email WHERE user_id = %%s AND active = true "
                "AND %s" % where, [self.owner.id] + list(params))
            par_le_sql = {r[0] for r in self.env.cr.fetchall()}
            self.assertEqual(par_le_domaine, par_le_sql, folder)
            self.assertNotIn(self.perso.id, par_le_sql, folder)

    def test_the_list_view_filter_carries_the_leaf(self):
        view = self.env.ref("bf_email_management.bf_email_view_search")
        self.assertIn(LEAF_XML, view.arch_db or "")

    def test_the_window_action_carries_the_leaf(self):
        action = self.env.ref("bf_email_management.bf_email_action")
        self.assertIn(LEAF_XML, action.domain or "")

    def test_the_systray_javascript_carries_the_leaf(self):
        from odoo.modules.module import get_module_path
        module_path = get_module_path("bf_email_systray", display_warning=False)
        if not module_path:
            self.skipTest("bf_email_systray absent du chemin d'addons")
        path = os.path.join(module_path, "static", "src", "js",
                            "bf_email_systray.js")
        blob = re.sub(r"\s+", "", open(path, encoding="utf-8").read())
        self.assertIn('["account_id","notany",[["active","=",false]]]', blob)

    # ------------------------------------------------------------------
    # Plus aucune connexion
    # ------------------------------------------------------------------
    def test_opening_an_inactive_account_is_refused(self):
        self._desactiver()
        with patch(f"{IMAP}.open_connection") as ouvrir:
            with self.assertRaises(bf_email_imap.ImapConnectionError):
                self.wild._ouvrir_imap()
        ouvrir.assert_not_called()

    def test_handling_a_row_does_not_touch_its_server(self):
        self._desactiver()
        with patch(f"{IMAP}.open_connection") as ouvrir:
            self.perso.with_user(self.owner).action_archive()
        ouvrir.assert_not_called()
        self.perso.invalidate_recordset()
        self.assertTrue(self.perso.is_handled,
                        "le geste aboutit dans Odoo même sans le serveur")

    def test_an_active_account_still_connects(self):
        # Pas de régression : la garde ne vise que le compte désactivé.
        with patch(f"{IMAP}.open_connection") as ouvrir:
            self.wild._ouvrir_imap()
        ouvrir.assert_called_once()
