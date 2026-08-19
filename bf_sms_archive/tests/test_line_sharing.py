"""Partage d'une ligne entre collègues, et origine du message (18.0.5.9.0).

Deux promesses tenues ici :

1. Une ligne partagée donne à un collègue le droit d'envoyer depuis ce numéro et
   de lire les conversations qui s'y tiennent — sans lui ouvrir les fils tenus
   sur les autres lignes du propriétaire, ni les fils marqués « Confidentiel ».
2. Chaque message porte sa ligne d'origine jusqu'à la Messagerie, pour qu'on
   voie depuis quel numéro l'échange a eu lieu avant de répondre.
"""

from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged("bf_sms_archive", "post_install", "-at_install")
class TestLineSharing(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.owner = new_test_user(
            cls.env, login="sms_owner", groups="bf_sms_archive.group_sms_user",
        )
        cls.mate = new_test_user(
            cls.env, login="sms_mate", groups="bf_sms_archive.group_sms_user",
        )
        cls.stranger = new_test_user(
            cls.env, login="sms_stranger", groups="bf_sms_archive.group_sms_user",
        )
        Line = cls.env["sms.archive.line"]
        cls.shared_line = Line.create({
            "label": "Ligne partagée",
            "did": "5145550001",
            "owner_id": cls.owner.id,
            "user_ids": [(6, 0, cls.mate.ids)],
        })
        cls.private_line = Line.create({
            "label": "Ligne privée",
            "did": "5145550002",
            "owner_id": cls.owner.id,
        })

    def _thread(self, phone, line, hidden=False):
        return self.env["sms.archive.thread"].create({
            "phone_normalized": phone,
            "owner_id": self.owner.id,
            "line_id": line.id,
            "is_hidden": hidden,
        })

    def _message(self, thread, line, body="Bonjour", direction="in", **extra):
        vals = {
            "thread_id": thread.id,
            "message_hash": f"h-{thread.id}-{body}-{direction}",
            "direction": direction,
            "body": body,
            "date_sent": "2026-08-18 12:00:00",
            "line_id": line.id,
        }
        vals.update(extra)
        return self.env["sms.archive.message"].create(vals)

    # ── Volet 1 : partage ─────────────────────────────────────────

    def test_shared_line_is_visible_to_mate(self):
        lines = self.env["sms.archive.line"].with_user(self.mate).search([])
        self.assertIn(self.shared_line, lines)
        self.assertNotIn(self.private_line, lines)

    def test_shared_line_is_read_only_for_mate(self):
        with self.assertRaises(AccessError):
            self.shared_line.with_user(self.mate).write({"label": "Détournée"})

    def test_mate_reads_threads_of_shared_line_only(self):
        shared = self._thread("+15145551111", self.shared_line)
        private = self._thread("+15145552222", self.private_line)
        visible = self.env["sms.archive.thread"].with_user(self.mate).search([])
        self.assertIn(shared, visible)
        self.assertNotIn(private, visible)

    def test_hidden_thread_stays_with_its_owner(self):
        secret = self._thread("+15145553333", self.shared_line, hidden=True)
        visible = self.env["sms.archive.thread"].with_user(self.mate).search([])
        self.assertNotIn(secret, visible)
        with self.assertRaises(UserError):
            secret.with_user(self.mate)._check_messenger_access()

    def test_stranger_sees_nothing(self):
        shared = self._thread("+15145554444", self.shared_line)
        visible = self.env["sms.archive.thread"].with_user(self.stranger).search([])
        self.assertNotIn(shared, visible)

    def test_messages_follow_the_thread(self):
        shared = self._thread("+15145555555", self.shared_line)
        private = self._thread("+15145556666", self.private_line)
        msg_ok = self._message(shared, self.shared_line)
        msg_no = self._message(private, self.private_line)
        Msg = self.env["sms.archive.message"].with_user(self.mate)
        found = Msg.search([])
        self.assertIn(msg_ok, found)
        self.assertNotIn(msg_no, found)

    def test_messenger_lists_shared_threads_but_not_manager_wide(self):
        shared = self._thread("+15145557777", self.shared_line)
        self._message(shared, self.shared_line)
        rows = self.env["sms.archive.thread"].with_user(self.mate) \
            .get_messenger_threads()
        self.assertEqual([r["id"] for r in rows], [shared.id])
        self.assertTrue(rows[0]["is_shared"])
        self.assertEqual(rows[0]["owner_name"], self.owner.name)

    def test_get_lines_orders_own_first_and_never_defaults_to_a_shared_line(self):
        own = self.env["sms.archive.line"].create({
            "label": "Ligne du collègue",
            "did": "5145550003",
            "owner_id": self.mate.id,
        })
        self.shared_line.is_default = True
        rows = self.env["sms.archive.thread"].with_user(self.mate).get_lines()
        self.assertEqual([r["id"] for r in rows], [own.id, self.shared_line.id])
        shared_row = rows[1]
        self.assertTrue(shared_row["is_shared"])
        self.assertFalse(
            shared_row["is_default"],
            "une ligne partagée ne doit jamais s'imposer comme numéro par défaut",
        )

    def test_usable_by(self):
        self.assertTrue(self.shared_line._is_usable_by(self.mate))
        self.assertFalse(self.shared_line._is_usable_by(self.stranger))
        self.assertFalse(self.private_line._is_usable_by(self.mate))

    def test_notify_users_covers_the_line_but_not_hidden_threads(self):
        shared = self._thread("+15145558888", self.shared_line)
        self.assertEqual(shared._notify_users(), self.owner | self.mate)
        secret = self._thread("+15145559999", self.shared_line, hidden=True)
        self.assertEqual(secret._notify_users(), self.owner)

    def test_thread_takes_the_line_of_its_first_live_message(self):
        thread = self.env["sms.archive.thread"]._get_or_create(
            "+15145550110", self.owner.id, line_id=self.shared_line.id,
        )
        self.assertEqual(thread.line_id, self.shared_line)
        # une réponse depuis une autre ligne ne rebascule pas le partage
        again = self.env["sms.archive.thread"]._get_or_create(
            "+15145550110", self.owner.id, line_id=self.private_line.id,
        )
        self.assertEqual(again, thread)
        self.assertEqual(thread.line_id, self.shared_line)

    # ── Volet 2 : origine du message ──────────────────────────────

    def test_messenger_dict_carries_the_origin(self):
        thread = self._thread("+15145550120", self.shared_line)
        msg = self._message(thread, self.shared_line)
        data = msg._messenger_dict()
        self.assertEqual(data["line_id"], self.shared_line.id)
        self.assertEqual(data["line_label"], "Ligne partagée")
        self.assertEqual(data["line_did"], "+15145550001")
        self.assertEqual(data["sent_by"], "")

    def test_outgoing_names_the_colleague_who_wrote_it(self):
        thread = self._thread("+15145550130", self.shared_line)
        msg = self._message(
            thread, self.shared_line, body="Réponse", direction="out",
            sent_by_id=self.mate.id,
        )
        self.assertEqual(msg._messenger_dict()["sent_by"], self.mate.name)

    def test_owner_sending_on_his_own_line_stays_silent(self):
        thread = self._thread("+15145550140", self.shared_line)
        msg = self._message(
            thread, self.shared_line, body="Réponse", direction="out",
            sent_by_id=self.owner.id,
        )
        self.assertEqual(
            msg._messenger_dict()["sent_by"], "",
            "inutile de nommer l'expéditeur quand c'est le propriétaire du fil",
        )

    # ── Bout en bout : de l'entrant VOIP.ms à l'écran du collègue ──

    def test_incoming_on_shared_line_reaches_the_colleague(self):
        """Un entrant ingéré sur la ligne partagée doit atterrir chez le
        collègue, avec son origine lisible et son compteur de non-lus."""
        rec, created = self.env["sms.archive.message"]._ingest_one(
            phone_raw="514 555-0150",
            owner_id=self.owner.id,
            direction="in",
            body="Message reçu sur la ligne partagée",
            date_ms=1755500000000,
            batch_id="test-live",
            voipms_id="vm-24634-1",
            line_id=self.shared_line.id,
            delivery_state="received",
            is_read=False,
        )
        self.assertTrue(created)
        self.assertEqual(rec.thread_id.line_id, self.shared_line)

        Thread = self.env["sms.archive.thread"].with_user(self.mate)
        rows = Thread.get_messenger_threads()
        self.assertEqual([r["id"] for r in rows], [rec.thread_id.id])
        self.assertEqual(rows[0]["line_label"], "Ligne partagée")

        conv = Thread.get_conversation(rec.thread_id.id)
        self.assertEqual(len(conv["messages"]), 1)
        self.assertEqual(conv["messages"][0]["line_label"], "Ligne partagée")

    def test_unread_summary_counts_the_shared_line(self):
        self.env["sms.archive.message"]._ingest_one(
            phone_raw="514 555-0160",
            owner_id=self.owner.id,
            direction="in",
            body="Non lu",
            date_ms=1755500001000,
            batch_id="test-live",
            voipms_id="vm-24634-2",
            line_id=self.shared_line.id,
            is_read=False,
        )
        summary = self.env["sms.archive.thread"].with_user(self.mate) \
            .get_unread_summary()
        self.assertEqual(summary["total"], 1)

    def test_colleague_sends_from_the_shared_line(self):
        """``action_send`` accepte le collègue et impute le message au fil du
        propriétaire, en gardant trace de qui a écrit."""
        thread = self._thread("+15145550170", self.shared_line)
        self._message(thread, self.shared_line)
        Msg = self.env["sms.archive.message"].with_user(self.mate)
        # VOIP.ms n'est pas configuré au banc : l'envoi échoue, mais le message
        # est archivé — c'est le chemin d'autorisation qu'on éprouve ici.
        msg_id = Msg.action_send(self.shared_line.id, "+15145550170", "Réponse")
        msg = self.env["sms.archive.message"].browse(msg_id)
        self.assertEqual(msg.thread_id, thread)
        self.assertEqual(msg.sent_by_id, self.mate)
        self.assertEqual(msg.line_id, self.shared_line)

    def test_stranger_cannot_send_from_someone_elses_line(self):
        Msg = self.env["sms.archive.message"].with_user(self.stranger)
        with self.assertRaises(UserError):
            Msg.action_send(self.shared_line.id, "+15145550180", "Tentative")

    # ── Ce que le partage ne donne PAS ────────────────────────────

    def test_shared_line_does_not_expose_history_held_elsewhere(self):
        """Le cas qui a motivé la portée par ligne : un fil d'archive Android
        rattaché après coup à une ligne partagée ne doit pas livrer au collègue
        l'historique personnel qui n'a jamais transité par ce numéro."""
        Thread = self.env["sms.archive.thread"]
        Msg = self.env["sms.archive.message"]
        # un vieux fil importé : aucun message « live », donc aucune ligne
        old = Thread.create({
            "phone_normalized": "+15145550200",
            "owner_id": self.owner.id,
        })
        archived = Msg.create({
            "thread_id": old.id,
            "message_hash": "h-archive-1",
            "direction": "in",
            "body": "Message personnel de 2019",
            "date_sent": "2019-05-01 12:00:00",
        })
        self.assertFalse(old.line_id)

        # le contact écrit maintenant au numéro partagé
        live, created = Msg._ingest_one(
            phone_raw="+15145550200",
            owner_id=self.owner.id,
            direction="in",
            body="Bonjour, question d'affaires",
            date_ms=1755500002000,
            batch_id="test-live",
            voipms_id="vm-24634-3",
            line_id=self.shared_line.id,
        )
        self.assertTrue(created)
        self.assertEqual(live.thread_id, old, "le fil existant reçoit le message")
        self.assertEqual(old.line_id, self.shared_line, "le fil se rattache à la ligne")

        # le collègue voit le fil et le message d'affaires, jamais l'archive
        visible = Msg.with_user(self.mate).search([("thread_id", "=", old.id)])
        self.assertIn(live, visible)
        self.assertNotIn(archived, visible)

    def test_shared_reader_gets_a_scoped_preview_and_unread_count(self):
        """Aperçu et compteur de non-lus d'un fil partagé ne doivent rien dire
        des messages que le lecteur n'a pas le droit de lire."""
        Thread = self.env["sms.archive.thread"]
        Msg = self.env["sms.archive.message"]
        thread = self._thread("+15145550210", self.shared_line)
        Msg.create({
            "thread_id": thread.id,
            "message_hash": "h-scoped-prive",
            "direction": "in",
            "body": "Confidence sur la ligne privée",
            "date_sent": "2026-08-18 10:00:00",
            "line_id": self.private_line.id,
            "is_read": False,
        })
        Msg.create({
            "thread_id": thread.id,
            "message_hash": "h-scoped-partage",
            "direction": "in",
            "body": "Question d'affaires",
            "date_sent": "2026-08-18 09:00:00",
            "line_id": self.shared_line.id,
            "is_read": False,
        })
        rows = Thread.with_user(self.mate).get_messenger_threads()
        row = next(r for r in rows if r["id"] == thread.id)
        self.assertEqual(row["last_preview"], "Question d'affaires")
        self.assertEqual(row["unread_count"], 1)
        self.assertEqual(row["hidden_count"], 1)
        # le propriétaire, lui, voit tout
        own = next(
            r for r in Thread.with_user(self.owner).get_messenger_threads()
            if r["id"] == thread.id
        )
        self.assertEqual(own["unread_count"], 2)
        self.assertEqual(own["hidden_count"], 0)

    def test_unread_summary_ignores_messages_on_unshared_lines(self):
        thread = self._thread("+15145550220", self.shared_line)
        self._message(thread, self.private_line, body="Privé")
        self.env["sms.archive.message"].create({
            "thread_id": thread.id,
            "message_hash": "h-unread-prive",
            "direction": "in",
            "body": "Privé non lu",
            "date_sent": "2026-08-18 11:00:00",
            "line_id": self.private_line.id,
            "is_read": False,
        })
        summary = self.env["sms.archive.thread"].with_user(self.mate) \
            .get_unread_summary()
        self.assertEqual(summary["total"], 0)

    def test_call_log_never_follows_a_shared_line(self):
        thread = self._thread("+15145550230", self.shared_line)
        call = self.env["call.archive.call"].create({
            "thread_id": thread.id,
            "call_hash": "c-24634-1",
            "call_type": "incoming",
            "date": "2026-08-18 09:00:00",
            "duration": 30,
        })
        visible = self.env["call.archive.call"].with_user(self.mate).search([])
        self.assertNotIn(
            call, visible,
            "un appel ne porte pas de ligne : le partage d'un numéro ne doit "
            "pas ouvrir le journal d'appels du propriétaire",
        )

    def test_shared_reader_cannot_take_ownership_of_a_thread(self):
        thread = self._thread("+15145550240", self.shared_line)
        with self.assertRaises(UserError):
            thread.with_user(self.mate).write({"owner_id": self.mate.id})
        with self.assertRaises(UserError):
            thread.with_user(self.mate).write({"is_hidden": True})
        with self.assertRaises(UserError):
            thread.with_user(self.mate).write({"line_id": self.private_line.id})
        # les gestes légitimes restent permis
        thread.with_user(self.mate).write({"is_pinned": True})
        self.assertTrue(thread.is_pinned)

    def test_owner_still_manages_his_own_thread(self):
        thread = self._thread("+15145550250", self.shared_line)
        thread.with_user(self.owner).write({"is_hidden": True})
        self.assertTrue(thread.is_hidden)
