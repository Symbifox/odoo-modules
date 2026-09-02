"""Dossiers IMAP dans la boîte, et garde sur l'UID de la recopie (#24976).

Deux sujets, une même cause profonde : un UID IMAP n'a de sens que dans SA
boîte. La recopie s'en servait sans le vérifier, ce qui produisait des
« archivages fantômes » — Odoo dit traité, le message est toujours dans
l'INBOX — et c'est exactement la dérive rapportée par l'usager.

Les tests d'IMAP ne parlent jamais au réseau : ``FakeImap`` répond ce qu'un
serveur répondrait, y compris le ``OK`` menteur d'un ``UID COPY`` visant un
UID absent, qui est le cœur du défaut.
"""
import json
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import tagged

from odoo.addons.bf_email_management.models import bf_email_imap

from .common import MobileApiCase

IMAP = "odoo.addons.bf_email_management.models.bf_email_imap"


class FakeImap:
    """Serveur IMAP de papier, assez fidèle pour ce que le module lui demande.

    ``mailboxes`` fait correspondre un nom de boîte à ses ``{uid: Message-ID}``.
    Les commandes lisent la boîte SELECTionnée, comme un vrai serveur — c'est
    tout l'enjeu : un UID n'a de sens que dans SA boîte. Une commande UID
    portant un UID absent répond ``OK`` sans rien faire (RFC 3501), et c'est
    ce ``OK`` menteur que la garde éprouvée ici doit rendre inoffensif.
    """

    def __init__(self, mailboxes=None, inbox=None, folders=None,
                 fail_copy=False, unselectable=()):
        self.mailboxes = {k: dict(v) for k, v in (mailboxes or {}).items()}
        if inbox is not None:
            self.mailboxes["INBOX"] = dict(inbox)
        self.mailboxes.setdefault("INBOX", {})
        self.folders = folders or []
        self.fail_copy = fail_copy
        self.unselectable = set(unselectable)
        self.calls = []
        self.copied = []
        self.deleted = set()
        self.selected = "INBOX"
        self._next_uid = 900

    # -- protocole ----------------------------------------------------
    def select(self, mailbox, readonly=True):
        name = mailbox.strip('"')
        self.calls.append(("SELECT", name))
        if name in self.unselectable:
            return ("NO", [b"[NONEXISTENT] no such mailbox"])
        self.selected = name
        self.mailboxes.setdefault(name, {})
        return ("OK", [b"1"])

    def list(self):
        return ("OK", list(self.folders))

    def _current(self):
        return self.mailboxes.setdefault(self.selected, {})

    def uid(self, command, *args):
        self.calls.append((command,) + args)
        box = self._current()
        if command == "FETCH":
            # Un UID ou un jeu de UID : `fetch_headers_bulk` en demande cent
            # à la fois.
            out = []
            for uid in str(args[0]).split(","):
                mid = box.get(uid)
                if mid is None:
                    continue
                body = ("Message-ID: %s\r\n\r\n" % mid).encode()
                prefix = b"1 (UID %s FLAGS () BODY[HEADER.FIELDS (MESSAGE-ID)] {%d}" % (
                    uid.encode(), len(body))
                out.append((prefix, body))
            if not out:
                return ("OK", [b")"])
            out.append(b")")
            return ("OK", out)
        if command == "SEARCH":
            if "HEADER" in args:
                wanted = args[-1]
                hits = [u for u, mid in box.items() if mid == wanted]
            else:
                # ALL / SINCE / UID n:* — la boîte entière suffit ici.
                hits = list(box)
            return ("OK", [" ".join(sorted(hits)).encode()])
        if command == "COPY":
            uid, target = str(args[0]), args[1].strip('"')
            if self.fail_copy:
                return ("NO", [b"[TRYCREATE] Mailbox doesn't exist"])
            if uid not in box:
                # Le OK menteur : rien n'est copié, personne n'est prévenu.
                return ("OK", [b""])
            self._next_uid += 1
            self.mailboxes.setdefault(target, {})[str(self._next_uid)] = box[uid]
            self.copied.append((uid, target))
            return ("OK", [b""])
        if command == "STORE":
            self.deleted.add((self.selected, str(args[0])))
            return ("OK", [b""])
        return ("OK", [b""])

    def expunge(self):
        self.calls.append(("EXPUNGE",))
        for box_name, uid in list(self.deleted):
            self.mailboxes.get(box_name, {}).pop(uid, None)
            self.deleted.discard((box_name, uid))
        return ("OK", [b""])

    def logout(self):
        return ("BYE", [b""])

    # -- confort de test ----------------------------------------------
    def commands(self, name):
        return [c for c in self.calls if c[0] == name]


@tagged("post_install", "-at_install")
class TestUidGuard(MobileApiCase):
    """La recopie ne doit jamais faire confiance à un UID non vérifié."""

    def _run_archive(self, fake):
        with patch(f"{IMAP}.open_connection", return_value=fake):
            self.inbound._imap_writeback_archive()
        self.inbound.invalidate_recordset()

    def test_a_matching_uid_is_used_directly(self):
        # Cas nominal : l'UID stocké porte bien ce Message-ID, aucune
        # recherche par en-tête n'a lieu.
        fake = FakeImap(inbox={"101": self.inbound.message_id_header})
        self._run_archive(fake)
        self.assertEqual(fake.copied, [("101", "Archives/2026")])
        self.assertFalse(
            fake.commands("SEARCH"),
            "un UID vérifié rend la recherche par en-tête inutile",
        )
        self.assertEqual(self.inbound.imap_folder, "Archives/2026")
        self.assertFalse(self.inbound.imap_in_inbox)

    def test_a_stale_uid_falls_back_to_the_header_search(self):
        # L'UID 101 de la ligne ne désigne rien ici ; le message est sous
        # l'UID 777. Sans la garde, COPY 101 répondait OK sans rien copier
        # et la ligne enregistrait un archivage qui n'a pas eu lieu.
        fake = FakeImap(inbox={"777": self.inbound.message_id_header})
        self._run_archive(fake)
        self.assertTrue(fake.commands("SEARCH"),
                        "un UID périmé doit déclencher la recherche")
        self.assertEqual(fake.copied, [("777", "Archives/2026")])
        self.assertEqual(
            self.inbound.imap_uid, "777",
            "la ligne doit repartir avec l'UID réellement déplacé",
        )
        self.assertFalse(self.inbound.imap_in_inbox)

    def test_a_message_absent_everywhere_is_left_alone(self):
        # Ni sous son UID, ni retrouvable par en-tête : on ne touche à rien
        # plutôt que d'écrire un archivage imaginaire.
        fake = FakeImap(inbox={"999": "<quelqu-un-dautre@test.invalid>"})
        self._run_archive(fake)
        self.assertFalse(fake.copied)
        self.assertEqual(self.inbound.imap_folder, "INBOX")
        self.assertTrue(self.inbound.imap_in_inbox)

    def test_a_refused_copy_still_leaves_the_message_in_place(self):
        # Garde de 18.0.6.7.0, non régressée : COPY refusé ⇒ pas de
        # \Deleted, pas d'écriture d'archivage.
        fake = FakeImap(inbox={"101": self.inbound.message_id_header},
                        fail_copy=True)
        with patch(f"{IMAP}.ensure_folder"):
            self._run_archive(fake)
        self.assertFalse(fake.commands("STORE"))
        self.assertEqual(self.inbound.imap_folder, "INBOX")

    def _archived_row(self):
        self.inbound.write({"imap_folder": "Archives/2026",
                            "imap_in_inbox": False,
                            "imap_uid": "555"})
        return self.inbound

    def test_restore_records_the_uid_the_message_gets_in_the_inbox(self):
        # Un COPY donne au message un NOUVEL UID dans la boîte d'arrivée :
        # celui du dossier d'archive ne veut plus rien dire.
        row = self._archived_row()
        fake = FakeImap(mailboxes={
            "Archives/2026": {"555": row.message_id_header},
        })
        with patch(f"{IMAP}.open_connection", return_value=fake):
            row._imap_writeback_restore()
        row.invalidate_recordset()
        self.assertEqual(row.imap_folder, "INBOX")
        self.assertTrue(row.imap_in_inbox)
        self.assertNotEqual(row.imap_uid, "555")
        self.assertEqual(
            fake.mailboxes["INBOX"].get(row.imap_uid),
            row.message_id_header,
            "l'UID enregistré doit désigner ce message DANS l'INBOX",
        )

    def test_restore_clears_an_unresolvable_uid(self):
        # INBOX injoignable après la copie : plutôt que d'hériter l'UID du
        # dossier d'archive — ce qui fabriquait l'UID périmé du test
        # précédent — la ligne repart sans UID du tout.
        row = self._archived_row()
        fake = FakeImap(
            mailboxes={"Archives/2026": {"555": row.message_id_header}},
            unselectable={"INBOX"},
        )
        with patch(f"{IMAP}.open_connection", return_value=fake):
            row._imap_writeback_restore()
        row.invalidate_recordset()
        self.assertEqual(row.imap_folder, "INBOX")
        self.assertFalse(
            row.imap_uid,
            "sans UID connu en INBOX, le champ doit être vidé — pas hérité",
        )


@tagged("post_install", "-at_install")
class TestUidVerificationHelper(MobileApiCase):

    def test_says_yes_only_on_a_real_match(self):
        fake = FakeImap(inbox={"12": "<vrai@test.invalid>"})
        self.assertTrue(bf_email_imap.uid_carries_message_id(
            fake, "12", "<vrai@test.invalid>"))
        self.assertFalse(bf_email_imap.uid_carries_message_id(
            fake, "12", "<autre@test.invalid>"))

    def test_an_absent_uid_is_a_no_not_an_error(self):
        fake = FakeImap(inbox={})
        self.assertIs(bf_email_imap.uid_carries_message_id(
            fake, "12", "<vrai@test.invalid>"), False)

    def test_chevrons_and_folding_do_not_break_the_comparison(self):
        fake = FakeImap(inbox={"12": "<vrai@test.invalid>"})
        self.assertTrue(bf_email_imap.uid_carries_message_id(
            fake, "12", "vrai@test.invalid"))

    def test_an_unreachable_server_answers_nothing_rather_than_no(self):
        class Broken(FakeImap):
            def uid(self, command, *args):
                raise OSError("connexion coupée")

        self.assertIsNone(bf_email_imap.uid_carries_message_id(
            Broken(), "12", "<vrai@test.invalid>"))


@tagged("post_install", "-at_install")
class TestListFolders(MobileApiCase):

    def test_quoted_hierarchy_and_flags_are_all_read(self):
        fake = FakeImap(folders=[
            b'(\\HasNoChildren) "/" "INBOX"',
            b'(\\HasChildren \\Noselect) "/" "Archives"',
            b'(\\HasNoChildren) "/" "Archives/2026"',
        ])
        folders = bf_email_imap.list_folders(fake)
        self.assertEqual([f["name"] for f in folders],
                         ["INBOX", "Archives", "Archives/2026"])
        self.assertTrue(folders[1]["noselect"])
        self.assertTrue(folders[1]["has_children"])
        self.assertEqual(folders[2]["delimiter"], "/")

    def test_a_name_with_a_space_keeps_its_whole_name(self):
        # `rsplit(None, 1)` rendait « clients » pour « Anciens clients ».
        fake = FakeImap(folders=[b'(\\HasNoChildren) "/" "Anciens clients"'])
        self.assertEqual(
            [f["name"] for f in bf_email_imap.list_folders(fake)],
            ["Anciens clients"],
        )

    def test_an_unquoted_name_is_accepted(self):
        fake = FakeImap(folders=[b'(\\HasNoChildren) "." Sent'])
        folders = bf_email_imap.list_folders(fake)
        self.assertEqual(folders[0]["name"], "Sent")
        self.assertEqual(folders[0]["delimiter"], ".")

    def test_a_broken_server_gives_an_empty_list_not_an_exception(self):
        class Broken(FakeImap):
            def list(self):
                raise OSError("coupé")

        self.assertEqual(bf_email_imap.list_folders(Broken()), [])


@tagged("post_install", "-at_install")
class TestInboxImapFolderTree(MobileApiCase):

    def setUp(self):
        super().setUp()
        self.account.write({
            "folder_cache": json.dumps([
                {"name": "INBOX", "delimiter": "/",
                 "has_children": False, "noselect": False},
                {"name": "Archives", "delimiter": "/",
                 "has_children": True, "noselect": True},
                {"name": "Archives/2026", "delimiter": "/",
                 "has_children": False, "noselect": False},
            ]),
            "folder_cache_date": "2999-01-01 00:00:00",
        })

    def _tree(self):
        return {f["key"]: f
                for f in self.as_owner().inbox_get_folders()}

    def test_the_group_and_its_folders_appear_under_the_inbox(self):
        tree = self._tree()
        self.assertIn("imapfolders", tree)
        self.assertFalse(tree["imapfolders"]["selectable"],
                         "le groupe se déplie, il ne s'ouvre pas")
        key = "imapf:%s:Archives/2026" % self.account.id
        self.assertIn(key, tree)
        self.assertEqual(tree[key]["label"], "2026",
                         "un sous-dossier porte son nom court dans l'arbre")
        self.assertEqual(tree[key]["parent"],
                         "imapf:%s:Archives" % self.account.id)

    def test_the_group_sits_right_under_the_inbox(self):
        keys = [f["key"] for f in self.as_owner().inbox_get_folders()]
        self.assertEqual(keys[0], "inbox")
        self.assertEqual(
            keys[1], "imapfolders",
            "l'usager les a demandés « sous la boîte de réception »",
        )

    def test_a_noselect_parent_is_not_openable(self):
        tree = self._tree()
        self.assertFalse(
            tree["imapf:%s:Archives" % self.account.id]["selectable"])

    def test_counts_come_from_the_rows_not_from_the_server(self):
        tree = self._tree()
        inbox_key = "imapf:%s:INBOX" % self.account.id
        # Trois lignes du propriétaire portent imap_folder=INBOX.
        self.assertEqual(tree[inbox_key]["count"], 3)
        self.assertEqual(tree[inbox_key]["unread_count"], 2)

    def test_a_colleague_row_is_never_counted(self):
        self.foreign.write({"account_id": self.account.id,
                            "imap_folder": "INBOX", "imap_uid": "900"})
        tree = self._tree()
        self.assertEqual(
            tree["imapf:%s:INBOX" % self.account.id]["count"], 3,
            "le compteur reste celui du propriétaire",
        )

    def test_opening_a_folder_scopes_to_the_owner_and_the_account(self):
        domain = self.as_owner()._inbox_folder_domain(
            "imapf:%s:INBOX" % self.account.id)
        self.assertIn(("user_id", "=", self.owner.id), domain)
        self.assertIn(("account_id", "=", self.account.id), domain)
        self.assertIn(("imap_folder", "=", "INBOX"), domain)

    def test_a_key_naming_someone_elses_account_is_refused(self):
        other = self.env["bf.email.account"].create({
            "name": "Boîte du voisin", "user_id": self.stranger.id,
            "host": "imap.test.invalid", "port": 993,
            "login": "stranger@test.invalid", "password": "x",
        })
        with self.assertRaises(UserError):
            self.as_owner()._inbox_folder_domain("imapf:%s:INBOX" % other.id)

    def test_the_tree_never_calls_the_server_while_the_cache_holds(self):
        with patch(f"{IMAP}.open_connection") as conn:
            self.as_owner().inbox_get_folders()
        self.assertFalse(conn.called)

    def test_opening_a_folder_never_calls_the_server(self):
        # Le domaine d'un dossier est résolu sans passer par la découverte :
        # sinon un cache échu ferait payer un aller-retour IMAP au clic.
        self.account.folder_cache_date = "2000-01-01 00:00:00"
        with patch(f"{IMAP}.open_connection") as conn:
            self.as_owner()._inbox_folder_domain(
                "imapf:%s:INBOX" % self.account.id)
        self.assertFalse(conn.called)


@tagged("post_install", "-at_install")
class TestImapFolderSetting(MobileApiCase):

    def test_turning_it_off_removes_the_group_for_everyone(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_email.show_imap_folders", "0")
        keys = [f["key"] for f in self.as_owner().inbox_get_folders()]
        self.assertNotIn("imapfolders", keys)

    def test_an_imap_key_is_refused_while_the_group_is_off(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_email.show_imap_folders", "0")
        with self.assertRaises(UserError):
            self.as_owner()._inbox_folder_domain(
                "imapf:%s:INBOX" % self.account.id)

    def test_unchecking_the_box_survives_the_save(self):
        # ⚠️ Avec `config_parameter`, décocher SUPPRIME le paramètre et le
        # défaut du code (« 1 ») le rallumerait au rechargement.
        settings = self.env["res.config.settings"].create({
            "bf_email_show_imap_folders": False,
        })
        settings.execute()
        self.assertEqual(
            self.env["ir.config_parameter"].sudo().get_param(
                "bf_email.show_imap_folders"),
            "0",
        )
        self.assertFalse(self.env["bf.email"]._inbox_imap_folders_enabled())
        self.assertFalse(
            self.env["res.config.settings"].create({}).get_values()[
                "bf_email_show_imap_folders"],
            "la case doit revenir décochée",
        )

    def test_checking_it_back_on_writes_a_one(self):
        ICP = self.env["ir.config_parameter"].sudo()
        ICP.set_param("bf_email.show_imap_folders", "0")
        self.env["res.config.settings"].create({
            "bf_email_show_imap_folders": True,
        }).execute()
        self.assertEqual(ICP.get_param("bf_email.show_imap_folders"), "1")
        self.assertTrue(self.env["bf.email"]._inbox_imap_folders_enabled())


@tagged("post_install", "-at_install")
class TestFolderCache(MobileApiCase):

    def test_the_cache_is_served_without_touching_the_server(self):
        self.account.write({
            "folder_cache": json.dumps([{"name": "INBOX", "delimiter": "/",
                                         "has_children": False,
                                         "noselect": False}]),
            "folder_cache_date": "2999-01-01 00:00:00",
        })
        with patch(f"{IMAP}.open_connection") as conn:
            folders = self.account._get_imap_folders()
        self.assertFalse(conn.called)
        self.assertEqual([f["name"] for f in folders], ["INBOX"])

    def test_a_stale_cache_is_refreshed_and_rewritten(self):
        self.account.write({
            "folder_cache": json.dumps([{"name": "VIEUX", "delimiter": "/",
                                         "has_children": False,
                                         "noselect": False}]),
            "folder_cache_date": "2000-01-01 00:00:00",
        })
        fake = FakeImap(folders=[b'(\\HasNoChildren) "/" "INBOX"'])
        with patch(f"{IMAP}.open_connection", return_value=fake):
            folders = self.account._get_imap_folders()
        self.assertEqual([f["name"] for f in folders], ["INBOX"])
        self.assertEqual(
            [f["name"] for f in json.loads(self.account.folder_cache)],
            ["INBOX"],
        )

    def test_an_unreachable_server_keeps_the_last_known_tree(self):
        # L'arbre des dossiers est un confort de navigation : il n'a pas à
        # faire tomber la boîte de réception avec le serveur de courriel.
        self.account.write({
            "folder_cache": json.dumps([{"name": "INBOX", "delimiter": "/",
                                         "has_children": False,
                                         "noselect": False}]),
            "folder_cache_date": "2000-01-01 00:00:00",
        })
        with patch(f"{IMAP}.open_connection",
                   side_effect=bf_email_imap.ImapConnectionError("nope")):
            folders = self.account._get_imap_folders()
        self.assertEqual([f["name"] for f in folders], ["INBOX"])

    def test_the_mirror_cron_keeps_the_cache_warm(self):
        # Le rendu de la colonne de gauche ne doit jamais avoir à ouvrir une
        # session IMAP : le cron qui passe déjà toutes les 5 minutes relève
        # l'arborescence pendant qu'il tient la connexion.
        self.account.write({"folder_cache": False, "folder_cache_date": False})
        fake = FakeImap(folders=[
            b'(\\HasNoChildren) "/" "INBOX"',
            b'(\\HasNoChildren) "/" "Anciens clients"',
        ])
        with patch(f"{IMAP}.open_connection", return_value=fake):
            self.env["bf.email"]._cron_imap_mirror()
        self.account.invalidate_recordset()
        self.assertEqual(
            [f["name"] for f in json.loads(self.account.folder_cache)],
            ["INBOX", "Anciens clients"],
        )
        self.assertTrue(self.account.folder_cache_date)

    def test_an_empty_list_does_not_wipe_a_useful_cache(self):
        self.account.write({
            "folder_cache": json.dumps([{"name": "INBOX", "delimiter": "/",
                                         "has_children": False,
                                         "noselect": False}]),
            "folder_cache_date": "2000-01-01 00:00:00",
        })
        with patch(f"{IMAP}.open_connection", return_value=FakeImap(folders=[])):
            folders = self.account._get_imap_folders()
        self.assertEqual([f["name"] for f in folders], ["INBOX"])


@tagged("post_install", "-at_install")
class TestSweepAcrossMailboxes(MobileApiCase):
    """Deux boîtes pour un même usager, et une adresse livrée aux deux.

    Le balayage retrouve les lignes par ``user_id`` — donc il voit dans
    l'INBOX du compte A une copie dont la ligne suit le compte B. Il passait
    ensuite la réparation à ``_imap_writeback_move``, qui se reconnecte à la
    boîte de la LIGNE, n'y trouve rien, et rend la main. Le message restait
    dans l'INBOX du compte A pour toujours, rejoué toutes les heures sans
    effet et sans avertissement. Constaté en production BF le 2026-08-26 sur
    trois courriels.
    """

    def setUp(self):
        super().setUp()
        # Compte B : autre boîte, même personne. Recopie désactivée, comme en
        # production — le balayage ne la parcourt donc jamais de son côté.
        self.other_box = self.env["bf.email.account"].create({
            "name": "bonjour@ — réception",
            "user_id": self.owner.id,
            "host": "imap.test.invalid",
            "port": 993,
            "login": "bonjour@test.invalid",
            "password": "x",
            "archive_folder": "Archive",
            "writeback_archive": False,
        })
        self.account.writeback_archive = True
        # La ligne suit le compte B et se croit déjà classée là-bas.
        self.inbound.write({
            "account_id": self.other_box.id,
            "is_handled": True,
            "imap_folder": "Archive",
            "imap_uid": "1715",
            "imap_in_inbox": False,
        })
        # …mais la copie physique traîne dans l'INBOX du compte A.
        self.box_a = FakeImap(inbox={"4242": self.inbound.message_id_header})
        self.box_b = FakeImap(mailboxes={"Archive": {"1715": self.inbound.message_id_header}})

    def _dispatch(self, host, port, login, password, timeout=30):
        return self.box_b if "bonjour" in login else self.box_a

    def _sweep(self, **kw):
        with patch(f"{IMAP}.open_connection", side_effect=self._dispatch):
            return self.env["bf.email"]._cron_imap_writeback_sweep(**kw)

    def test_the_gap_is_seen(self):
        gaps = self._sweep(dry_run=True)
        self.assertEqual(gaps.get(self.account.id), [self.inbound.id])
        self.assertFalse(self.box_a.copied, "une simulation ne déplace rien")

    def test_the_copy_is_filed_in_the_mailbox_where_it_was_found(self):
        self._sweep()
        self.assertEqual(
            self.box_a.copied, [("4242", "Archives/2026")],
            "la copie doit sortir de l'INBOX du compte qui la porte, "
            "avec le dossier d'archive de CE compte",
        )
        self.assertFalse(
            self.box_a.mailboxes["INBOX"],
            "l'INBOX observée doit être vide après le balayage",
        )

    def test_the_row_keeps_describing_its_own_copy(self):
        # Écraser imap_uid avec le 4242 du compte A fabriquerait un UID
        # périmé pour la boîte que la ligne suit vraiment.
        self._sweep()
        self.inbound.invalidate_recordset()
        self.assertEqual(self.inbound.account_id, self.other_box)
        self.assertEqual(self.inbound.imap_uid, "1715")
        self.assertEqual(self.inbound.imap_folder, "Archive")

    def test_a_second_pass_finds_nothing_left(self):
        self._sweep()
        self.assertEqual(self._sweep(dry_run=True), {},
                         "le balayage doit converger, pas se répéter")

    def test_a_row_of_the_swept_account_still_behaves_as_before(self):
        # Pas de régression sur le cas ordinaire : la ligne du compte balayé
        # est bien réécrite, elle.
        self.inbound.write({
            "account_id": self.account.id,
            "imap_folder": "INBOX",
            "imap_uid": "4242",
            "imap_in_inbox": True,
        })
        self._sweep()
        self.inbound.invalidate_recordset()
        self.assertEqual(self.inbound.imap_folder, "Archives/2026")
        self.assertFalse(self.inbound.imap_in_inbox)
        self.assertEqual(self.inbound.imap_uid, "4242")
