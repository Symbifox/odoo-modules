from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged


@tagged("bf_bloc_notes")
class TestBfNote(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Note = cls.env["bf.note"]
        cls.Link = cls.env["bf.note.link"]
        cls.Activity = cls.env["mail.activity"]
        cls.partner = cls.env["res.partner"].create({"name": "Test Partner Notes"})
        cls.user_a = cls.env["res.users"].create({
            "name": "User A",
            "login": "user_a_notes_test",
            "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])],
        })
        cls.user_b = cls.env["res.users"].create({
            "name": "User B",
            "login": "user_b_notes_test",
            "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])],
        })

    def test_auto_title_from_body(self):
        n = self.Note.create({"body": "<p>Hello world</p>"})
        self.assertEqual(n.name, "Hello world")

    def test_create_with_link(self):
        n = self.Note.create({
            "body": "<p>Linked</p>",
            "link_ids": [(0, 0, {"res_model": "res.partner", "res_id": self.partner.id})],
        })
        self.assertEqual(len(n.link_ids), 1)
        self.assertEqual(n.res_model, "res.partner")
        self.assertEqual(n.res_id, self.partner.id)
        self.assertEqual(n.res_name, self.partner.display_name)

    def test_smart_button_count_uses_batch(self):
        self.Note.create({
            "body": "<p>One</p>",
            "link_ids": [(0, 0, {"res_model": "res.partner", "res_id": self.partner.id})],
        })
        self.Note.create({
            "body": "<p>Two</p>",
            "link_ids": [(0, 0, {"res_model": "res.partner", "res_id": self.partner.id})],
        })
        self.partner.invalidate_recordset(["bf_note_count"])
        self.assertEqual(self.partner.bf_note_count, 2)

    def test_quick_create_whitelists_user_id(self):
        """RPC must reject arbitrary user_id and force env.user."""
        result = self.Note.with_user(self.user_a).quick_create_from_context({
            "body": "<p>RPC injection attempt</p>",
            "user_id": self.user_b.id,  # malicious
            "create_uid": self.user_b.id,  # malicious
            "res_model": "res.partner",
            "res_id": self.partner.id,
        })
        note = self.Note.browse(result["id"])
        self.assertEqual(note.user_id, self.user_a, "user_id must be forced to caller")

    def test_visibility_private_by_default(self):
        n = self.Note.with_user(self.user_a).create({"body": "<p>Private</p>"})
        self.assertFalse(n.is_shared)
        # User B cannot read
        with self.assertRaises(AccessError):
            n.with_user(self.user_b).read(["body"])

    def test_visibility_shared_readable_by_others(self):
        n = self.Note.with_user(self.user_a).create({
            "body": "<p>Shared</p>",
            "is_shared": True,
        })
        # User B can read
        body = n.with_user(self.user_b).read(["body"])
        self.assertEqual(len(body), 1)

    def test_visibility_shared_not_writable_by_others(self):
        n = self.Note.with_user(self.user_a).create({
            "body": "<p>Shared</p>",
            "is_shared": True,
        })
        with self.assertRaises(AccessError):
            n.with_user(self.user_b).write({"body": "<p>Hijacked</p>"})

    def test_create_activity_quick_creates_one_per_link(self):
        n = self.Note.create({
            "body": "<p>Activity test</p>",
            "link_ids": [
                (0, 0, {"res_model": "res.partner", "res_id": self.partner.id}),
            ],
        })
        n.with_context(offset=2).action_create_activity_quick()
        self.assertEqual(n.tracked_activity_count, 1)
        act = n.tracked_activity_ids
        expected = fields.Date.context_today(n) + timedelta(days=2)
        self.assertEqual(act.date_deadline, expected)
        self.assertEqual(act.summary, n.name)
        self.assertEqual(act.res_model, "res.partner")
        self.assertEqual(act.res_id, self.partner.id)

    def test_create_activity_quick_without_link_falls_back_on_self(self):
        n = self.Note.create({"body": "<p>No link</p>"})
        n.action_create_activity_quick()
        self.assertEqual(n.tracked_activity_count, 1)
        act = n.tracked_activity_ids
        self.assertEqual(act.res_model, "bf.note")
        self.assertEqual(act.res_id, n.id)

    def test_link_res_name_respects_acl(self):
        """display_name leak fix: a private partner the user cannot read
        must not surface its display_name through bf.note.link.res_name."""
        # Create a private partner, only readable by user_a (not user_b).
        # We use ir.rule isolation via a simple proxy: user_b has a fresh
        # access right but res.partner is normally readable by all internal
        # users — so we instead simulate by linking to a record under an
        # access rule we can deny. Use res.users which has stricter rules
        # in some configs; here we assert the no-leak path via mocked
        # check_access_rule.
        from unittest.mock import patch

        n = self.Note.with_user(self.user_a).create({
            "body": "<p>Linked to restricted</p>",
            "link_ids": [(0, 0, {"res_model": "res.partner", "res_id": self.partner.id})],
        })
        # Force ACL to refuse
        with patch.object(type(self.env["res.partner"]), "check_access",
                          side_effect=AccessError("denied")):
            n.invalidate_recordset(["link_ids"])
            link = n.link_ids[:1]
            link.invalidate_recordset(["res_name"])
            self.assertFalse(link.res_name, "res_name must be empty when ACL denies read")
