"""« Synchroniser maintenant » fait aussi tourner le miroir.

Des courriels archivés dans Thunderbird restaient « à faire » dans Odoo après
un clic sur le bouton, qui ne lançait que l'ingestion. Le miroir, seul à
constater qu'un message a quitté l'INBOX, n'attendait que sa passe aux cinq
minutes.
"""
from unittest.mock import patch

from odoo.tests import tagged

from .test_imap_folders_and_uid_guard import FakeImap
from .test_miroir_symetrique import IMAP, MiroirCase

BF_EMAIL = "odoo.addons.bf_email_management.models.bf_email.BfEmail"


@tagged("post_install", "-at_install")
class TestSynchroniserLanceLeMiroir(MiroirCase):

    def _click(self, user, fake):
        # L'ingestion n'est pas l'objet de ces essais : FakeImap ne sait pas
        # FETCH, et `_sync_own_accounts` a ses propres essais.
        with patch(f"{IMAP}.open_connection", return_value=fake) as opened, \
                patch(f"{BF_EMAIL}._sync_own_accounts"):
            result = self.env["bf.email"].with_user(user).action_sync_now()
        self.inbound.invalidate_recordset()
        return result, opened

    def test_a_message_archived_elsewhere_is_handled_by_the_button(self):
        result, _opened = self._click(self.owner, FakeImap(inbox={}))
        self.assertFalse(self.inbound.imap_in_inbox)
        self.assertTrue(self.inbound.is_handled)
        self.assertIn("marqué(s) Traité(s)", result["params"]["message"])
        self.assertEqual(result["params"]["type"], "success")

    def test_the_inbox_list_button_says_it_too(self):
        with patch(f"{IMAP}.open_connection", return_value=FakeImap(inbox={})), \
                patch(f"{BF_EMAIL}._sync_own_accounts"):
            res = self.env["bf.email"].with_user(self.owner).inbox_sync_now()
        self.assertIn("1 courriel(s) rangé(s) hors d'Odoo", res["message"])

    def test_a_message_still_in_the_inbox_is_left_alone(self):
        fake = FakeImap(inbox={"101": self.inbound.message_id_header})
        result, _opened = self._click(self.owner, fake)
        self.assertTrue(self.inbound.imap_in_inbox)
        self.assertFalse(self.inbound.is_handled)
        self.assertNotIn("Traité(s)", result["params"]["message"])

    def test_another_user_never_opens_this_mailbox(self):
        # Le bouton est offert à tout usager interne : il n'ouvre que SES
        # boîtes, jamais celle d'un collègue avec le mot de passe stocké.
        _result, opened = self._click(self.stranger, FakeImap(inbox={}))
        opened.assert_not_called()
        self.assertTrue(self.inbound.imap_in_inbox)
        self.assertFalse(self.inbound.is_handled)

    def _half_done_then_crash(self, inbound):
        # Une passe qui a déjà écrit quand elle tombe : seul un point de
        # sauvegarde défait l'écriture ; un simple try/except la garderait.
        def crash(_self, _account):
            inbound.sudo().write({"is_handled": True})
            inbound.env.flush_all()
            raise RuntimeError("panne simulée")
        return crash

    def test_a_failing_mailbox_does_not_break_the_button(self):
        with patch(f"{BF_EMAIL}._imap_mirror_account",
                   new=self._half_done_then_crash(self.inbound)), \
                patch(f"{BF_EMAIL}._sync_own_accounts"):
            result = self.env["bf.email"].with_user(self.owner).action_sync_now()
        self.assertEqual(result["params"]["title"], "Synchronisation terminée")
        self.inbound.invalidate_recordset()
        self.assertFalse(self.inbound.is_handled,
                         "l'écriture d'une passe tombée doit être défaite")

    def test_a_failing_mailbox_does_not_break_the_cron(self):
        with patch(f"{BF_EMAIL}._imap_mirror_account",
                   new=self._half_done_then_crash(self.inbound)):
            self.env["bf.email"]._cron_imap_mirror()
        self.inbound.invalidate_recordset()
        self.assertFalse(self.inbound.is_handled)

    def test_a_colleague_with_a_mailbox_only_mirrors_their_own(self):
        account2 = self.env["bf.email.account"].create({
            "name": "Boîte du collègue", "user_id": self.stranger.id,
            "host": "imap.test.invalid", "port": 993,
            "login": "stranger@test.invalid", "password": "x",
            "state": "connected",
        })
        self.foreign.write({
            "account_id": account2.id, "imap_folder": "INBOX",
            "imap_uid": "201",
        })
        _result, opened = self._click(self.stranger, FakeImap(inbox={}))
        self.assertEqual(opened.call_count, 1, "sa boîte, et elle seule")
        self.foreign.invalidate_recordset()
        self.assertTrue(self.foreign.is_handled)
        self.assertTrue(self.inbound.imap_in_inbox,
                        "la boîte du propriétaire n'a pas été ouverte")
        self.assertFalse(self.inbound.is_handled)

    def test_the_cron_still_walks_every_account(self):
        # La passe par compte est sortie du cron : le cron doit toujours
        # couvrir tout le monde, propriétaire ou non de la session.
        with patch(f"{IMAP}.open_connection", return_value=FakeImap(inbox={})):
            self.env["bf.email"].with_user(self.stranger).sudo()._cron_imap_mirror()
        self.inbound.invalidate_recordset()
        self.assertTrue(self.inbound.is_handled)
