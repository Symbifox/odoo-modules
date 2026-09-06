"""Boucle Odoo → IMAP et indicateur de chatter.

Le défaut couvert ici a laissé des courriels dans l'INBOX du serveur pour de
bon : le re-routage avec « archiver après » écrivait ``active=False`` à la
main au lieu d'appeler ``action_archive()``. La ligne disparaissait de toutes
les vues d'Odoo, ``is_handled`` restait faux, et la recopie IMAP n'était
jamais déclenchée. Trois choses à tenir, donc : le re-routage passe par
l'action, le miroir voit les lignes désactivées, et un balayage rattrape ce
qui a échappé aux deux.
"""
from unittest.mock import patch

from odoo import fields
from odoo.addons.mail.tools.discuss import Store
from odoo.tests import tagged

from .common import MobileApiCase

MODEL = "odoo.addons.bf_email_management.models.bf_email.BfEmail"


@tagged("post_install", "-at_install")
class TestRerouteArchive(MobileApiCase):

    def setUp(self):
        super().setUp()
        # Router vers un contact demande le droit d'écriture dessus : le
        # sorcier passe par _get_chatter_target("write").
        self.owner.groups_id |= self.env.ref("base.group_partner_manager")

    def _wizard(self, rows, archive_after=True):
        return self.env["bf.email.reroute"].with_user(self.owner).create({
            "bf_email_ids": [(6, 0, rows.ids)],
            "target_reference": f"res.partner,{self.partner.id}",
            "archive_after": archive_after,
        })

    def test_archive_after_marks_handled_and_keeps_the_row(self):
        self.account.writeback_archive = False
        wizard = self._wizard(self.with_attachment)
        wizard.action_confirm()
        row = self.with_attachment
        self.assertTrue(row.is_handled, "le courriel doit compter comme traité")
        self.assertTrue(row.handled_at)
        self.assertTrue(
            row.active,
            "la ligne doit rester consultable : c'est le seul moyen de "
            "rattraper une recopie IMAP ratée",
        )
        self.assertNotEqual(
            row.status, "archived",
            "le statut 'archived' est une pierre tombale historique",
        )

    def test_archive_after_triggers_the_imap_writeback(self):
        # Le cœur du défaut : l'ancienne écriture directe ne déclenchait
        # jamais la recopie, donc le message restait dans l'INBOX.
        self.account.writeback_archive = True
        with patch(f"{MODEL}._imap_writeback_archive") as writeback:
            self._wizard(self.with_attachment).action_confirm()
        self.assertTrue(writeback.called)

    def test_without_archive_after_nothing_is_handled(self):
        self.account.writeback_archive = False
        self._wizard(self.with_attachment, archive_after=False).action_confirm()
        self.assertFalse(self.with_attachment.is_handled)


@tagged("post_install", "-at_install")
class TestMirrorSeesInactiveRows(MobileApiCase):

    def test_snoozed_wake_up_reaches_a_deactivated_row(self):
        # Une ligne désactivée par l'ancien re-routage restait endormie pour
        # toujours : la recherche du cron ignorait active=False.
        past = fields.Datetime.subtract(fields.Datetime.now(), hours=1)
        self.inbound.write({
            "is_handled": True,
            "snoozed_until": past,
            "active": False,
        })
        self.env["bf.email"]._cron_imap_mirror()
        self.inbound.invalidate_recordset()
        row = self.env["bf.email"].with_context(
            active_test=False).browse(self.inbound.id)
        self.assertFalse(row.is_handled, "le report est échu, elle doit revenir")
        self.assertFalse(row.snoozed_until)


@tagged("post_install", "-at_install")
class TestWritebackSweep(MobileApiCase):

    def test_sweep_survives_an_unreachable_server(self):
        # Aucun compte joignable ici : le balayage doit rendre la main sans
        # lever, sinon un incident réseau ferait échouer le cron entier.
        self.account.writeback_archive = True
        self.assertEqual(self.env["bf.email"]._cron_imap_writeback_sweep(), {})

    def test_sweep_ignores_accounts_without_writeback(self):
        self.account.writeback_archive = False
        with patch("odoo.addons.bf_email_management.models."
                   "bf_email_imap.open_connection") as conn:
            self.env["bf.email"]._cron_imap_writeback_sweep()
        self.assertFalse(
            conn.called,
            "un compte sans recopie ne doit même pas être contacté",
        )


@tagged("post_install", "-at_install")
class TestChatterBadge(MobileApiCase):

    def _state_of(self, message):
        store = Store()
        message.with_user(self.owner)._to_store(store, for_current_user=True)
        result = store.get_result()
        for entry in result.get("mail.message", []):
            if entry.get("id") == message.id:
                return entry.get("bfEmailState")
        return None

    def _message_mirroring(self, row):
        """Un mail.message portant le même Message-ID que ``row``."""
        return self.env["mail.message"].create({
            "model": "res.partner",
            "res_id": self.partner.id,
            "message_type": "email",
            "subtype_id": self.env.ref("mail.mt_comment").id,
            "body": "<p>corps</p>",
            "message_id": row.message_id_header,
        })

    def test_untreated_mail_reads_as_inbox(self):
        message = self._message_mirroring(self.inbound)
        self.assertEqual(self._state_of(message), "inbox")

    def test_treated_mail_reads_as_handled(self):
        self.account.writeback_archive = False
        self.inbound.action_archive()
        message = self._message_mirroring(self.inbound)
        self.assertEqual(self._state_of(message), "handled")

    def test_snoozed_mail_reads_as_snoozed(self):
        self.inbound.write({
            "is_handled": True,
            "snoozed_until": fields.Datetime.add(
                fields.Datetime.now(), days=1),
        })
        message = self._message_mirroring(self.inbound)
        self.assertEqual(self._state_of(message), "snoozed")

    def test_a_message_without_mirror_carries_no_state(self):
        message = self.env["mail.message"].create({
            "model": "res.partner",
            "res_id": self.partner.id,
            "message_type": "email",
            "subtype_id": self.env.ref("mail.mt_comment").id,
            "body": "<p>sans miroir</p>",
            "message_id": "<aucun-miroir@test.invalid>",
        })
        self.assertFalse(self._state_of(message))

    def test_a_stranger_mirror_is_not_borrowed(self):
        # L'état est strictement personnel : le miroir du voisin ne doit pas
        # décider de ce que je vois.
        message = self._message_mirroring(self.foreign)
        self.assertFalse(self._state_of(message))
