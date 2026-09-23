from odoo.exceptions import AccessError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase, new_test_user


@tagged("post_install", "-at_install")
class TestCancelNote(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = new_test_user(cls.env, "bf_cancel_user", groups="base.group_user")
        cls.other = new_test_user(cls.env, "bf_cancel_other", groups="base.group_user")
        cls.manager = new_test_user(
            cls.env, "bf_cancel_manager", groups="base.group_user,base.group_partner_manager"
        )
        cls.partner = cls.env["res.partner"].create({"name": "BF cancel note"})
        cls.todo = cls.env.ref("mail.mail_activity_data_todo")

    def _activity(self, record=None, user=None, **vals):
        record = record or self.partner
        return self.env["mail.activity"].create({
            "res_model_id": self.env["ir.model"]._get_id(record._name),
            "res_id": record.id,
            "activity_type_id": self.todo.id,
            "summary": "Call the client back",
            "note": "<p>About the renewal</p>",
            "user_id": (user or self.user).id,
            **vals,
        })

    def _cancel_notes(self, record):
        return record.message_ids.filtered(
            lambda m: "fa-ban" in (m.body or "")
        )

    def test_cancel_with_note_posts_and_removes(self):
        activity = self._activity()
        activity.with_user(self.user).action_cancel_with_note(reason="Client said no")
        self.assertFalse(activity.exists())
        notes = self._cancel_notes(self.partner)
        self.assertEqual(len(notes), 1)
        note = notes[0]
        self.assertIn("Call the client back", note.body)
        self.assertIn("About the renewal", note.body)
        self.assertIn("Client said no", note.body)
        self.assertEqual(note.mail_activity_type_id, self.todo)
        self.assertEqual(note.subtype_id, self.env.ref("mail.mt_activities"))
        self.assertEqual(note.author_id, self.user.partner_id)

    def test_discard_leaves_no_trace(self):
        activity = self._activity()
        before = len(self.partner.message_ids)
        activity.with_user(self.user).unlink()
        self.assertFalse(activity.exists())
        self.assertEqual(len(self.partner.message_ids), before)

    def test_attachments_follow_the_note(self):
        activity = self._activity()
        attachment = self.env["ir.attachment"].create({
            "name": "brief.txt", "raw": b"x",
            "res_model": "mail.activity", "res_id": activity.id,
        })
        activity.with_user(self.user).action_cancel_with_note()
        note = self._cancel_notes(self.partner)
        self.assertTrue(attachment.exists())
        self.assertEqual(attachment.res_model, "mail.message")
        self.assertEqual(attachment.res_id, note.id)

    def test_no_chained_activity_is_created(self):
        follow = self.env["mail.activity.type"].create({"name": "BF follow-up"})
        chained = self.env["mail.activity.type"].create({
            "name": "BF chained", "chaining_type": "trigger",
            "triggered_next_type_id": follow.id,
        })
        activity = self._activity(activity_type_id=chained.id)
        activity.with_user(self.user).action_cancel_with_note()
        self.assertFalse(self.partner.activity_ids)

    def test_access_checked_before_posting(self):
        # An activity assigned to someone else, on a record the user cannot
        # write (base.group_user has no write ACL on res.partner): refused,
        # and nothing stays in the chatter. This proves the outcome, not the
        # order: without the early check_access, unlink() still refuses and the
        # rollback erases the note (mutation, 2026-09-22).
        private = self.env["res.partner"].create({"name": "BF private"})
        activity = self._activity(record=private, user=self.other)
        before = len(private.message_ids)
        with self.assertRaises(AccessError):
            activity.with_user(self.user).action_cancel_with_note(reason="leak?")
        self.assertTrue(activity.exists())
        self.assertEqual(len(private.message_ids), before)

    def test_archiving_the_record_leaves_a_note(self):
        activity = self._activity()
        self.partner.with_user(self.manager).write({"active": False})
        self.assertFalse(activity.exists())
        note = self._cancel_notes(self.partner)
        self.assertEqual(len(note), 1)
        self.assertIn("The record was archived.", note.body)
        self.assertEqual(note.author_id, self.manager.partner_id)

    def test_action_archive_leaves_a_note(self):
        # action_archive goes through toggle_active, which unlinks before write.
        activity = self._activity()
        self.partner.with_user(self.manager).action_archive()
        self.assertFalse(activity.exists())
        self.assertEqual(len(self._cancel_notes(self.partner)), 1)

    def test_archiving_project_skips_done_keep_done_activities(self):
        # project.project.write archives its tasks with active_test=False: the
        # search then also returns archived (done) keep_done activities, which
        # must not be noted as cancelled.
        if "project.project" not in self.env:
            self.skipTest("project is not installed")
        keep = self.env["mail.activity.type"].create({"name": "BF keep", "keep_done": True})
        project = self.env["project.project"].create({"name": "BF project"})
        task = self.env["project.task"].create({"name": "BF task", "project_id": project.id})
        done = self._activity(record=task, activity_type_id=keep.id)
        done.action_feedback(feedback="done")
        self.assertFalse(done.with_context(active_test=False).active)
        pending = self._activity(record=task)
        project.write({"active": False})
        self.assertFalse(pending.exists())
        notes = self._cancel_notes(task)
        self.assertEqual(len(notes), 1)
        self.assertIn("Call the client back", notes.body)

    def test_keep_done_archived_activity_not_noted(self):
        keep = self.env["mail.activity.type"].create({"name": "BF keep", "keep_done": True})
        activity = self._activity(activity_type_id=keep.id)
        activity.with_user(self.user).action_feedback(feedback="done")
        self.assertFalse(activity.with_context(active_test=False).active)
        self.partner.write({"active": False})
        self.assertFalse(self._cancel_notes(self.partner))
