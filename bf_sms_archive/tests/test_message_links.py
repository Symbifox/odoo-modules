"""Le registre des rattachements (18.0.5.13.0).

Ce qui change et se vérifie ici : un même message peut viser plusieurs fiches,
le registre le sait, reposter ne crée pas de doublon, défaire retire la note
seulement quand plus aucun message ne la vise, et le suivi automatique d'un fil
nourrit désormais plusieurs tâches.
"""

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged("bf_sms_archive", "post_install", "-at_install")
class TestMessageLinks(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.thread = cls.env["sms.archive.thread"].create({
            "phone_normalized": "+15145550202",
            "contact_name": "Client à deux dossiers",
            "owner_id": cls.env.user.id,
        })
        cls.msg_a, cls.msg_b = cls.env["sms.archive.message"].create([
            {
                "thread_id": cls.thread.id,
                "message_hash": "hash-links-a",
                "direction": "in",
                "body": "Le toit coule, et le bail se renouvelle quand ?",
                "date_sent": "2026-09-07 12:00:00",
            },
            {
                "thread_id": cls.thread.id,
                "message_hash": "hash-links-b",
                "direction": "out",
                "body": "Je réponds sur les deux séparément.",
                "date_sent": "2026-09-07 12:05:00",
            },
        ])
        cls.project = cls.env["project.project"].create({"name": "Projet liens SMS"})
        cls.toiture, cls.bail = cls.env["project.task"].create([
            {"name": "Réfection de toiture", "project_id": cls.project.id},
            {"name": "Renouvellement de bail", "project_id": cls.project.id},
        ])
        cls.partner = cls.env["res.partner"].create({"name": "Fiche contact SMS"})
        cls.Link = cls.env["sms.archive.link"]

    def _notes(self, record):
        return self.env["mail.message"].search([
            ("model", "=", record._name), ("res_id", "=", record.id),
            ("message_type", "=", "comment"),
        ])

    # ── Le cœur de la demande ──────────────────────────────────────
    def test_one_message_reaches_two_tasks(self):
        """Le cœur de la demande : le même SMS concerne deux dossiers."""
        self.msg_a._post_to_record(self.toiture)
        self.msg_a._post_to_record(self.bail)
        refs = set(self.msg_a.link_ids.mapped("res_ref"))
        self.assertEqual(refs, {
            f"project.task,{self.toiture.id}", f"project.task,{self.bail.id}",
        })
        self.assertEqual(self.msg_a.link_count, 2)
        self.assertEqual(len(self._notes(self.toiture)), 1)
        self.assertEqual(len(self._notes(self.bail)), 1)

    def test_reposting_refreshes_instead_of_duplicating(self):
        self.msg_a._post_to_record(self.toiture)
        first_note = self.msg_a.link_ids.mail_message_id
        self.msg_a._post_to_record(self.toiture)
        self.assertEqual(len(self.msg_a.link_ids), 1)
        self.assertNotEqual(self.msg_a.link_ids.mail_message_id, first_note)

    def test_a_target_need_not_be_a_task(self):
        self.msg_a._post_to_record(self.partner)
        self.assertEqual(self.msg_a.link_ids.res_model, "res.partner")
        self.assertEqual(self.msg_a.link_ids.res_name, self.partner.display_name)
        # Le lien fil ↔ tâche ne connaît que les tâches : il doit rester vide.
        self.assertFalse(self.thread.task_ids)

    def test_task_counts_the_messages_posted_on_it(self):
        self.msg_a._post_to_record(self.toiture)
        self.msg_b._post_to_record(self.toiture)
        self.msg_b._post_to_record(self.bail)
        self.toiture.invalidate_recordset(["sms_link_count"])
        self.bail.invalidate_recordset(["sms_link_count"])
        self.assertEqual(self.toiture.sms_link_count, 2)
        self.assertEqual(self.bail.sms_link_count, 1)

    def test_the_counter_does_not_confuse_models_sharing_an_id(self):
        """`res_ref` en un seul champ : sur un One2many, deux conditions
        séparées (`res_model` d'un côté, `res_id` de l'autre) se seraient
        appliquées à deux lignes différentes, et le compteur de la tâche aurait
        ramassé la fiche d'un AUTRE modèle portant le même identifiant."""
        self.Link.create({
            "message_id": self.msg_a.id,
            "res_model": "res.partner",
            "res_id": self.toiture.id,
        })
        self.msg_a._post_to_record(self.bail)
        self.toiture.invalidate_recordset(["sms_link_count"])
        self.assertEqual(self.toiture.sms_link_count, 0)

    # ── Défaire ────────────────────────────────────────────────────
    def test_undo_removes_the_note_when_nothing_else_points_at_it(self):
        self.msg_a._post_to_record(self.toiture)
        note = self.msg_a.link_ids.mail_message_id
        self.msg_a.link_ids.action_undo()
        self.assertFalse(self.msg_a.link_ids)
        self.assertFalse(note.exists())

    def test_undo_keeps_a_note_that_still_covers_another_message(self):
        """Une note consolidée couvre plusieurs messages : retirer le premier
        rattachement ne doit pas faire disparaître le second du chatter."""
        both = self.msg_a | self.msg_b
        both._post_to_record(self.toiture)
        note = both.link_ids.mail_message_id
        self.assertEqual(len(note), 1)
        self.msg_a.link_ids.action_undo()
        self.assertTrue(note.exists())
        self.assertEqual(self.msg_b.link_ids.mail_message_id, note)
        self.msg_b.link_ids.action_undo()
        self.assertFalse(note.exists())

    # ── Suivi automatique pluriel ──────────────────────────────────
    def test_auto_follow_feeds_every_task_listed(self):
        self.thread.write({
            "auto_post_task_ids": [(6, 0, (self.toiture | self.bail).ids)],
        })
        self.msg_b._auto_post_to_task()
        self.assertEqual(len(self._notes(self.toiture)), 1)
        self.assertEqual(len(self._notes(self.bail)), 1)
        self.assertEqual(len(self.msg_b.link_ids), 2)
        self.assertTrue(all(self.msg_b.link_ids.mapped("is_auto")))

    def test_auto_follow_skips_an_archived_task(self):
        self.thread.write({
            "auto_post_task_ids": [(6, 0, (self.toiture | self.bail).ids)],
        })
        self.bail.active = False
        self.msg_b._auto_post_to_task()
        self.assertEqual(len(self._notes(self.toiture)), 1)
        self.assertEqual(len(self._notes(self.bail)), 0)

    def test_the_wizard_adds_to_the_follow_list_instead_of_replacing_it(self):
        """Brancher un deuxième dossier ne doit pas débrancher le premier."""
        self.thread.write({"auto_post_task_ids": [(4, self.toiture.id, 0)]})
        wizard = self.env["sms.archive.post.to.task.wizard"].create({
            "message_ids": [(6, 0, self.msg_a.ids)],
            "target_reference": f"project.task,{self.bail.id}",
            "follow_thread": True,
        })
        wizard.action_post()
        self.assertEqual(self.thread.auto_post_task_ids, self.toiture | self.bail)

    # ── Points d'entrée de la Messagerie ───────────────────────────
    def test_messenger_posts_and_returns_the_refreshed_badges(self):
        Thread = self.env["sms.archive.thread"]
        res = Thread.messenger_post_to_target(
            self.thread.id, "project.task", self.toiture.id,
            message_ids=[self.msg_a.id],
        )
        self.assertEqual(res["count"], 1)
        # Clés en chaînes : c'est ce qui rend le retour transportable en XML-RPC.
        key = str(self.msg_a.id)
        self.assertEqual(len(res["links"][key]), 1)
        self.assertEqual(res["links"][key][0]["name"], self.toiture.display_name)

    def test_messenger_refuses_a_message_from_another_thread(self):
        """Les identifiants du client sont réduits au fil visé : glisser
        l'identifiant d'un message étranger ne doit rien poster."""
        other = self.env["sms.archive.thread"].create({
            "phone_normalized": "+15145550303", "owner_id": self.env.user.id,
        })
        stranger = self.env["sms.archive.message"].create({
            "thread_id": other.id, "message_hash": "hash-links-stranger",
            "direction": "in", "body": "Message d'un autre fil.",
            "date_sent": "2026-09-07 13:00:00",
        })
        with self.assertRaises(UserError):
            self.env["sms.archive.thread"].messenger_post_to_target(
                self.thread.id, "project.task", self.toiture.id,
                message_ids=[stranger.id],
            )
        self.assertFalse(stranger.link_ids)

    def test_messenger_toggles_one_task_at_a_time(self):
        Thread = self.env["sms.archive.thread"]
        res = Thread.messenger_set_auto_task(self.thread.id, self.toiture.id)
        self.assertEqual(res["auto_task_ids"], [self.toiture.id])
        res = Thread.messenger_set_auto_task(self.thread.id, self.bail.id)
        self.assertEqual(set(res["auto_task_ids"]),
                         {self.toiture.id, self.bail.id})
        res = Thread.messenger_set_auto_task(self.thread.id, self.toiture.id)
        self.assertEqual(res["auto_task_ids"], [self.bail.id])
        res = Thread.messenger_set_auto_task(self.thread.id, False)
        self.assertEqual(res["auto_task_ids"], [])

    def test_conversation_carries_the_badges(self):
        self.msg_a._post_to_record(self.toiture)
        res = self.env["sms.archive.thread"].get_conversation(self.thread.id)
        by_id = {m["id"]: m for m in res["messages"]}
        self.assertEqual(len(by_id[self.msg_a.id]["links"]), 1)
        self.assertEqual(by_id[self.msg_b.id]["links"], [])

    def test_a_followed_task_can_still_be_deleted(self):
        """Brancher le suivi ne doit pas rendre une tâche indestructible."""
        self.thread.write({"auto_post_task_ids": [(4, self.bail.id, 0)]})
        self.bail.unlink()
        self.assertFalse(self.thread.auto_post_task_ids)

    def test_a_deleted_target_does_not_break_the_registry(self):
        self.msg_a._post_to_record(self.bail)
        link = self.msg_a.link_ids
        self.bail.unlink()
        self.assertTrue(link.exists())
        self.assertEqual(link.res_name, "Fiche supprimée")
        with self.assertRaises(UserError):
            link.action_open_target()
