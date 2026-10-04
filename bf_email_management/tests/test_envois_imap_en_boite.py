"""Un envoi fait depuis un autre client entre en boîte comme un envoi d'Odoo.

Le cas d'origine : une réponse envoyée depuis Thunderbird. La copie du dossier
« Sent » est captée en moins d'une minute, mais n'apparaît pas dans la boîte
bf_email : une ligne IMAP n'y entrait que si le message était physiquement
dans l'INBOX, alors qu'un envoi fait depuis Odoo y entre. Désormais, même
règle des deux côtés, et ranger la copie au serveur la traite.
"""
import imaplib
import json
from unittest.mock import patch

from odoo.tests import tagged

from .common import MobileApiCase
from .test_imap_folders_and_uid_guard import FakeImap

IMAP = "odoo.addons.bf_email_management.models.bf_email_imap"


class _RechercheRefusee(FakeImap):
    """Un serveur qui bute sur la recherche d'UN Message-ID précis.

    ``reponse`` : « BAD » (imaplib lève ``error``), « NO » (réponse refusée)
    ou « abort » (connexion perdue au milieu de la passe).
    """

    def __init__(self, refuse, reponse="BAD", **kw):
        super().__init__(**kw)
        self.refuse = refuse
        self.reponse = reponse

    def uid(self, command, *args):
        if command == "SEARCH" and "HEADER" in args and args[-1] == self.refuse:
            self.calls.append((command,) + args)
            if self.reponse == "NO":
                return ("NO", [b"search refused"])
            if self.reponse == "abort":
                raise imaplib.IMAP4.abort("socket error: EOF")
            raise imaplib.IMAP4.error("SEARCH command error: BAD")
        return super().uid(command, *args)


class _SearchRefuse(FakeImap):
    """Un serveur qui répond NO à SEARCH : la passe ne doit rien conclure."""

    def uid(self, command, *args):
        if command == "SEARCH":
            self.calls.append((command,) + args)
            return ("NO", [b"server busy"])
        return super().uid(command, *args)


@tagged("post_install", "-at_install")
class TestEnvoiImapEnBoite(MobileApiCase):

    def _envoyer(self, message_id, reply_to=None, uid=4242,
                 date="Wed, 02 Sep 2026 15:00:00 +0000"):
        """La copie « Sent » d'un envoi fait depuis un client de courriel."""
        entetes = [
            "Message-ID: %s" % message_id,
            "From: owner@test.invalid",
            "To: client@acme.test",
            "Subject: Re: Question sur la facture",
            "Date: %s" % date,
        ]
        if reply_to:
            entetes += ["In-Reply-To: %s" % reply_to.message_id_header,
                        "References: %s %s" % (reply_to.thread_root_id,
                                               reply_to.message_id_header)]
        raw = ("\r\n".join(entetes) + "\r\n\r\nMerci.\r\n").encode()
        self.as_owner()._ingest_rfc822(raw, uid, "Sent", self.account)
        return self.env["bf.email"].with_context(active_test=False).search(
            [("message_id_header", "=", message_id)], limit=1)

    def _inbox_ids(self):
        page = self.as_owner().inbox_get_messages("inbox", 0, 500)
        return {m["id"] for m in page["messages"]}

    # ------------------------------------------------------------------
    # Entrer en boîte
    # ------------------------------------------------------------------
    def test_a_reply_in_a_thread_still_in_the_inbox_enters_the_inbox(self):
        # Le cas d'origine : la question est encore en boîte.
        copie = self._envoyer("<rep-1@test.invalid>", reply_to=self.inbound)
        self.assertEqual(copie.direction, "out")
        self.assertEqual(copie.thread_root_id, self.inbound.thread_root_id)
        self.assertFalse(copie.is_handled)
        self.assertIn(copie.id, self._inbox_ids())

    def test_a_new_mail_enters_the_inbox(self):
        # Comme un courriel neuf composé dans Odoo.
        copie = self._envoyer("<neuf-1@test.invalid>")
        self.assertFalse(copie.is_handled)
        self.assertIn(copie.id, self._inbox_ids())

    def test_a_reply_in_a_treated_thread_is_born_handled(self):
        # Gmail ne ramène pas un fil archivé sur un envoi ; Odoo non plus
        # depuis la 18.0.11.52.0, et Thunderbird non plus maintenant.
        (self.inbound | self.outbound).write({"is_handled": True})
        copie = self._envoyer("<rep-2@test.invalid>", reply_to=self.inbound)
        self.assertTrue(copie.is_handled)
        self.assertNotIn(copie.id, self._inbox_ids())

    def test_the_phone_agrees_with_the_tree(self):
        copie = self._envoyer("<rep-3@test.invalid>", reply_to=self.inbound)
        BfEmail = self.as_owner()
        par_le_domaine = set(BfEmail.search(
            BfEmail._inbox_domain() + [("user_id", "=", self.owner.id)]).ids)
        where, params = BfEmail._mobile_filter_sql("inbox")
        self.env.flush_all()
        self.env.cr.execute(
            "SELECT id FROM bf_email WHERE user_id = %%s AND active = true "
            "AND %s" % where, [self.owner.id] + list(params))
        par_le_sql = {r[0] for r in self.env.cr.fetchall()}
        self.assertEqual(par_le_domaine, par_le_sql)
        self.assertIn(copie.id, par_le_sql)

    # ------------------------------------------------------------------
    # Ranger au serveur la traite
    # ------------------------------------------------------------------
    def test_archiving_the_sent_copy_handles_it(self):
        copie = self._envoyer("<rep-4@test.invalid>", reply_to=self.inbound)
        fake = FakeImap(mailboxes={"Sent": {}})
        n = self.env["bf.email"]._imap_mirror_sent(fake, self.account)
        copie.invalidate_recordset()
        self.assertEqual(n, 1)
        self.assertTrue(copie.is_handled)
        self.assertNotIn(copie.id, self._inbox_ids())

    def test_a_sent_copy_still_there_is_untouched(self):
        copie = self._envoyer("<rep-5@test.invalid>", reply_to=self.inbound)
        fake = FakeImap(mailboxes={"Sent": {"4242": copie.message_id_header}})
        n = self.env["bf.email"]._imap_mirror_sent(fake, self.account)
        copie.invalidate_recordset()
        self.assertEqual(n, 0)
        self.assertFalse(copie.is_handled)

    def test_a_renumbered_sent_copy_is_reanchored_not_handled(self):
        # UIDVALIDITY changé : même message, autre numéro.
        copie = self._envoyer("<rep-6@test.invalid>", reply_to=self.inbound)
        fake = FakeImap(mailboxes={"Sent": {"9000": copie.message_id_header}})
        n = self.env["bf.email"]._imap_mirror_sent(fake, self.account)
        copie.invalidate_recordset()
        self.assertEqual(n, 0)
        self.assertFalse(copie.is_handled)
        self.assertEqual(copie.imap_uid, "9000")

    def test_an_unreadable_server_handles_nothing(self):
        copie = self._envoyer("<rep-7@test.invalid>", reply_to=self.inbound)
        for fake in (FakeImap(unselectable={"Sent"}),
                     _SearchRefuse(mailboxes={"Sent": {}})):
            n = self.env["bf.email"]._imap_mirror_sent(fake, self.account)
            copie.invalidate_recordset()
            self.assertEqual(n, 0)
            self.assertFalse(copie.is_handled,
                             "une coupure ne doit pas vider la boîte")

    def test_no_sent_copy_to_follow_means_no_select(self):
        # Le cas courant : rien à suivre, on ne touche pas au dossier.
        fake = FakeImap(mailboxes={"Sent": {}})
        self.env["bf.email"]._imap_mirror_sent(fake, self.account)
        self.assertFalse(fake.commands("SEARCH"))

    def test_the_mirror_cron_runs_the_sent_pass(self):
        # Les autres comptes de la base ne doivent pas partager le faux serveur.
        self.env["bf.email.account"].sudo().search(
            [("id", "!=", self.account.id)]).write({"active": False})
        copie = self._envoyer("<rep-8@test.invalid>", reply_to=self.inbound)
        fake = FakeImap(mailboxes={
            "INBOX": {"101": self.inbound.message_id_header,
                      "102": self.outbound.message_id_header,
                      "103": self.with_attachment.message_id_header},
            "Sent": {},
        })
        with patch(f"{IMAP}.open_connection", return_value=fake):
            self.env["bf.email"]._cron_imap_mirror()
        copie.invalidate_recordset()
        self.inbound.invalidate_recordset()
        self.assertTrue(copie.is_handled)
        self.assertFalse(self.inbound.is_handled,
                         "l'INBOX n'a pas bougé, la question reste en boîte")

    # ------------------------------------------------------------------
    # Les gestes ne déplacent pas nos envois sur le serveur
    # ------------------------------------------------------------------
    def test_handling_a_sent_copy_moves_nothing(self):
        self.account.writeback_archive = True
        copie = self._envoyer("<rep-9@test.invalid>", reply_to=self.inbound)
        fake = FakeImap(mailboxes={"Sent": {"4242": copie.message_id_header}})
        with patch(f"{IMAP}.open_connection", return_value=fake):
            copie.with_user(self.owner).action_archive()
        copie.invalidate_recordset()
        self.assertTrue(copie.is_handled)
        self.assertFalse(fake.copied)

    def test_putting_it_back_does_not_copy_it_to_the_inbox(self):
        self.account.writeback_archive = True
        copie = self._envoyer("<rep-10@test.invalid>", reply_to=self.inbound)
        copie.write({"is_handled": True})
        fake = FakeImap(mailboxes={"Sent": {"4242": copie.message_id_header}})
        with patch(f"{IMAP}.open_connection", return_value=fake):
            copie.with_user(self.owner).action_unhandle()
        copie.invalidate_recordset()
        self.assertFalse(fake.copied, "notre envoi n'a rien à faire dans l'INBOX")
        self.assertFalse(copie.imap_folder,
                         "remise en boîte, la copie oublie son UID périmé (11.54.1)")
        self.assertIn(copie.id, self._inbox_ids())

    def test_restore_called_directly_skips_sent_copies(self):
        copie = self._envoyer("<rep-11@test.invalid>", reply_to=self.inbound)
        fake = FakeImap(mailboxes={"Sent": {"4242": copie.message_id_header}})
        with patch(f"{IMAP}.open_connection", return_value=fake):
            copie._imap_writeback_restore()
        self.assertFalse(fake.copied)

    # ------------------------------------------------------------------
    # 18.0.11.54.1 : les constats des relectures adverses de la 11.54.0
    # ------------------------------------------------------------------
    def _migration_11541(self):
        import importlib.util
        from odoo.modules.module import get_module_path
        chemin = (get_module_path("bf_email_management")
                  + "/migrations/18.0.11.54.1/post-migrate.py")
        spec = importlib.util.spec_from_file_location("bf_email_migration_11_54_1", chemin)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        return migration

    def test_the_upgrade_does_not_pour_the_sent_backlog_into_the_inbox(self):
        """Une montée depuis la 11.50 ne verse pas les vieilles copies en boîte."""
        vieille = self._envoyer("<arriere-1@test.invalid>")
        chatter = self.env["bf.email"].sudo().create({
            "subject": "Merci", "email_from": "owner@test.invalid",
            "email_to": "client@acme.test", "direction": "out",
            "status": "read", "source": "chatter", "user_id": self.owner.id,
            "message_id_header": "<arriere-chatter@test.invalid>",
            "thread_root_id": "<arriere-chatter@test.invalid>",
            "imap_folder": "Sent", "date": "2026-08-10 12:00:00",
        })
        self.assertFalse(vieille.is_handled)
        self._migration_11541().migrate(self.env.cr, "18.0.11.50.0")
        vieille.invalidate_recordset()
        chatter.invalidate_recordset()
        self.assertTrue(vieille.is_handled)
        self.assertNotIn(vieille.id, self._inbox_ids())
        self.assertFalse(chatter.is_handled,
                         "une ligne née du chatter était déjà en boîte avant")

    def test_a_base_already_on_11540_is_left_alone(self):
        copie = self._envoyer("<arriere-2@test.invalid>")
        self._migration_11541().migrate(self.env.cr, "18.0.11.54.0")
        copie.invalidate_recordset()
        self.assertFalse(copie.is_handled)
        self.assertIn(copie.id, self._inbox_ids())

    def test_putting_back_a_copy_filed_on_the_server_survives_the_mirror(self):
        """Rangée au serveur, traitée, remise en boîte : le miroir ne l'annule plus."""
        self.account.writeback_archive = True
        copie = self._envoyer("<rangee-1@test.invalid>", reply_to=self.inbound)
        fake = FakeImap(mailboxes={"Sent": {}})
        self.env["bf.email"]._imap_mirror_sent(fake, self.account)
        copie.invalidate_recordset()
        self.assertTrue(copie.is_handled)
        self.assertEqual(copie.imap_folder, "Sent", "la passe 3 garde l'emplacement")
        with patch(f"{IMAP}.open_connection", return_value=fake):
            copie.with_user(self.owner).action_unhandle()
        self.assertFalse(fake.copied)
        self.assertIn(copie.id, self._inbox_ids())
        self.env["bf.email"]._imap_mirror_sent(fake, self.account)
        copie.invalidate_recordset()
        self.assertFalse(copie.is_handled, "le miroir annulait la remise en boîte")
        self.assertIn(copie.id, self._inbox_ids())

    def _deux_copies(self, prefixe):
        """Deux copies disparues de « Sent » ; la fautive passe EN PREMIER
        (plus récente : l'ordre du modèle est ``date desc``)."""
        fautive = self._envoyer("<%s-fautive@test.invalid>" % prefixe, uid=4301,
                                date="Wed, 02 Sep 2026 16:00:00 +0000")
        saine = self._envoyer("<%s-saine@test.invalid>" % prefixe, uid=4302)
        return fautive, saine

    def _passe_3(self, fautive, saine, reponse):
        fake = _RechercheRefusee(fautive.message_id_header, reponse,
                                 mailboxes={"Sent": {}})
        n = self.env["bf.email"]._imap_mirror_sent(fake, self.account)
        (fautive | saine).invalidate_recordset()
        cherches = [c[-1] for c in fake.commands("SEARCH") if "HEADER" in c]
        self.assertEqual(cherches[0], fautive.message_id_header,
                         "l'essai exige que la fautive passe la première")
        return n

    def test_one_refused_message_id_does_not_freeze_the_account(self):
        for reponse in ("BAD", "NO"):
            fautive, saine = self._deux_copies("refus-%s" % reponse.lower())
            n = self._passe_3(fautive, saine, reponse)
            self.assertEqual(n, 1, reponse)
            self.assertFalse(fautive.is_handled, "un refus ne conclut rien")
            self.assertTrue(saine.is_handled, "la passe continue après un refus")
            (fautive | saine).write({"is_handled": True})

    def test_a_lost_connection_stops_the_sent_pass_without_concluding(self):
        fautive, saine = self._deux_copies("coupure")
        n = self._passe_3(fautive, saine, "abort")
        self.assertEqual(n, 0)
        self.assertFalse(fautive.is_handled)
        self.assertFalse(saine.is_handled, "une connexion perdue arrête la passe")

    def test_a_handled_copy_dragged_back_to_the_inbox_returns(self):
        """Traitée par la passe 3, puis glissée dans l'INBOX au serveur : le
        miroir la reconnaît à son UID gardé et la remet en boîte."""
        copie = self._envoyer("<retour-1@test.invalid>")
        self.env["bf.email"]._imap_mirror_sent(FakeImap(mailboxes={"Sent": {}}),
                                               self.account)
        copie.invalidate_recordset()
        self.assertTrue(copie.is_handled)
        fake = FakeImap(inbox={"777": copie.message_id_header})
        retours, _ancres = self.env["bf.email"]._imap_mirror_adopt(
            fake, self.account, ["777"])
        copie.invalidate_recordset()
        self.assertEqual(retours, 1)
        self.assertFalse(copie.is_handled)
        self.assertTrue(copie.imap_in_inbox)

    def test_handled_in_odoo_then_filed_then_put_back_sticks(self):
        """« Traité » dans Odoo (rien ne bouge au serveur), rangée ensuite au
        serveur, puis remise en boîte : le miroir ne la retraite pas."""
        self.account.writeback_archive = True
        copie = self._envoyer("<odoo-puis-tb@test.invalid>", reply_to=self.inbound)
        vide = FakeImap(mailboxes={"Sent": {}})
        with patch(f"{IMAP}.open_connection", return_value=vide):
            copie.with_user(self.owner).action_archive()
            copie.with_user(self.owner).action_unhandle()
        self.env["bf.email"]._imap_mirror_sent(vide, self.account)
        copie.invalidate_recordset()
        self.assertFalse(copie.is_handled)
        self.assertIn(copie.id, self._inbox_ids())

    def test_a_lost_connection_in_the_sent_pass_does_not_sink_the_mirror(self):
        self.env["bf.email.account"].sudo().search(
            [("id", "!=", self.account.id)]).write({"active": False})
        fake = FakeImap(mailboxes={
            "INBOX": {"102": self.outbound.message_id_header,
                      "103": self.with_attachment.message_id_header},
        })
        with patch(f"{IMAP}.open_connection", return_value=fake), \
                patch.object(type(self.env["bf.email"]), "_imap_mirror_sent",
                             side_effect=OSError("timed out")):
            self.env["bf.email"]._cron_imap_mirror()
        self.inbound.invalidate_recordset()
        self.assertTrue(self.inbound.is_handled,
                        "la passe 2 du même passage est gardée")

    def _cache_gmail(self):
        self.account.sudo().folder_cache = json.dumps([
            {"name": "INBOX"}, {"name": "[Gmail]/Sent Mail", "special": ["\\Sent"]}])

    def test_restore_never_copies_our_sends_even_called_directly(self):
        """La garde de `_imap_writeback_restore` elle-même, emplacement intact."""
        self._cache_gmail()
        cas = [("Archives/2026", "out", "<direct-1@test.invalid>"),
               ("[Gmail]/Sent Mail", "in", "<direct-2@test.invalid>")]
        for dossier, sens, mid in cas:
            ligne = self._envoyer(mid, uid=4500 + len(dossier))
            ligne.write({"imap_folder": dossier, "direction": sens,
                         "is_handled": True})
            fake = FakeImap(mailboxes={dossier: {str(ligne.imap_uid): mid}})
            with patch(f"{IMAP}.open_connection", return_value=fake):
                ligne._imap_writeback_restore()
            self.assertFalse(fake.copied, dossier)

    def test_a_received_mail_is_still_restored_to_the_inbox(self):
        """Témoin : la garde ne bloque pas la restauration d'un courriel reçu."""
        self.account.writeback_archive = True
        recu = self.inbound
        recu.write({"imap_folder": "Archives/2026", "imap_in_inbox": False,
                    "is_handled": True})
        fake = FakeImap(mailboxes={"Archives/2026": {"55": recu.message_id_header}})
        with patch(f"{IMAP}.open_connection", return_value=fake):
            recu.with_user(self.owner).action_unhandle()
        self.assertTrue(fake.copied, "un courriel reçu retourne dans l'INBOX du serveur")

    def test_putting_back_a_send_filed_elsewhere_never_copies_it_to_the_inbox(self):
        """Archivé depuis Thunderbird, ou un dossier d'envoyés d'un autre nom."""
        self.account.writeback_archive = True
        self._cache_gmail()
        for dossier, sens, mid in (
                ("Archives/2026", "out", "<ailleurs-1@test.invalid>"),
                ("[Gmail]/Sent Mail", "in", "<ailleurs-2@test.invalid>")):
            ligne = self._envoyer(mid, uid=4400 + len(dossier))
            ligne.write({"imap_folder": dossier, "direction": sens,
                         "is_handled": True})
            fake = FakeImap(mailboxes={dossier: {str(ligne.imap_uid): mid}})
            with patch(f"{IMAP}.open_connection", return_value=fake):
                ligne.with_user(self.owner).action_unhandle()
            ligne.invalidate_recordset()
            self.assertFalse(fake.copied, dossier)
            self.assertIn(ligne.id, self._inbox_ids(), dossier)

    def test_the_dashboard_tile_counts_what_its_click_lists(self):
        copie = self._envoyer("<tuile-1@test.invalid>")
        Dash = self.env["bf.email.dashboard"].with_user(self.owner)
        tuile = Dash._get_actionable()["inbox_active"]
        clic = self.env["bf.email"].with_user(self.owner).search_count(
            Dash.action_view_inbox_active()["domain"])
        self.assertEqual(tuile, clic)
        self.assertIn(copie, self.env["bf.email"].with_user(self.owner).search(
            Dash.action_view_inbox_active()["domain"]))

    def test_our_own_sends_do_not_light_the_unread_pill(self):
        copie = self._envoyer("<pastille-1@test.invalid>")
        self.assertEqual(copie.status, "new")
        defs = {d["key"]: d for d in self.as_owner()._inbox_folder_defs()}
        Email = self.env["bf.email"].with_user(self.owner)
        self.assertNotIn(copie, Email.search(defs["inbox"]["unread_domain"]))
        self.account.sudo().own_inbox = True
        compte = next(d for d in self.as_owner()._inbox_account_defs()
                      if d["key"] == "inboxacct:%s" % self.account.id)
        self.assertIn(copie, Email.search(compte["domain"]))
        self.assertNotIn(copie, Email.search(compte["unread_domain"]),
                         "la boîte du compte suit la boîte commune")
        self.assertIn(copie.id, self._inbox_ids())

    # ------------------------------------------------------------------
    # Les recopies du domaine
    # ------------------------------------------------------------------
    def test_the_xml_copies_carry_the_leaf(self):
        leaf = "('imap_folder', '=ilike', 'Sent')"
        view = self.env.ref("bf_email_management.bf_email_view_search")
        self.assertIn(leaf, view.arch_db or "")
        action = self.env.ref("bf_email_management.bf_email_action")
        self.assertIn(leaf, action.domain or "")

    def test_the_systray_javascript_carries_the_leaf(self):
        import os
        import re
        from odoo.modules.module import get_module_path
        module_path = get_module_path("bf_email_systray", display_warning=False)
        if not module_path:
            self.skipTest("bf_email_systray absent du chemin d'addons")
        path = os.path.join(module_path, "static", "src", "js",
                            "bf_email_systray.js")
        blob = re.sub(r"\s+", "", open(path, encoding="utf-8").read())
        self.assertIn('["imap_folder","=ilike","Sent"]', blob)
