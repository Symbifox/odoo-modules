from datetime import date, timedelta
from unittest.mock import patch

from odoo import fields

from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from .common import WorkHoursCase


@tagged("post_install", "-at_install")
class TestShare(WorkHoursCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.carol = cls.env["res.users"].create({"name": "WH Carol", "login": "wh_carol",
                                                 "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])]})

    def setUp(self):
        super().setUp()
        self.day = self.Day.sudo().create({"user_id": self.alice.id, "date": date(2026, 10, 7), "hours": 7.5})
        self.trace = self.signal(self.alice, self.utc(2026, 10, 7, 14), 30)

    def visible(self, viewer):
        return self.Day.with_user(viewer).search([("user_id", "=", self.alice.id)])

    def test_nobody_else_sees_a_day_not_even_an_administrator(self):
        self.assertEqual(self.visible(self.alice), self.day)
        self.assertFalse(self.visible(self.bob))
        self.assertFalse(self.visible(self.admin))
        with self.assertRaises(AccessError):
            self.day.with_user(self.admin).read(["hours"])

    def test_a_named_colleague_sees_the_days_never_the_traces(self):
        self.Share.with_user(self.alice).create({"viewer_id": self.bob.id})
        self.assertEqual(self.visible(self.bob), self.day)
        self.assertFalse(self.visible(self.carol))
        self.assertFalse(self.Signal.with_user(self.bob).search([("user_id", "=", self.alice.id)]))
        with self.assertRaises(AccessError):
            self.day.with_user(self.bob).write({"hours": 1})

    def test_revoking_cuts_the_whole_history(self):
        share = self.Share.with_user(self.alice).create({"viewer_id": self.bob.id})
        share.with_user(self.alice).action_revoke()
        self.assertFalse(self.visible(self.bob))
        self.assertTrue(share.sudo().revoked_at)
        # Shared again later: a new live share beside the revoked one.
        self.Share.with_user(self.alice).create({"viewer_id": self.bob.id})
        self.assertEqual(self.visible(self.bob), self.day)

    def test_only_the_person_shares_and_revokes(self):
        with self.assertRaises(AccessError):
            self.Share.with_user(self.admin).create({"owner_id": self.alice.id, "viewer_id": self.admin.id})
        with self.assertRaises(AccessError):
            self.Share.with_user(self.admin).with_context(default_owner_id=self.alice.id).create(
                {"viewer_id": self.admin.id})
        share = self.Share.with_user(self.alice).create({"viewer_id": self.bob.id})
        with self.assertRaises(AccessError):
            share.with_user(self.bob).action_revoke()
        with self.assertRaises(UserError):
            share.with_user(self.alice).write({"viewer_id": self.carol.id})
        with self.assertRaises(UserError):
            self.Share.with_user(self.alice).create({"viewer_id": self.alice.id})

    def test_revoking_twice_keeps_the_first_date(self):
        share = self.Share.with_user(self.alice).create({"viewer_id": self.bob.id})
        share.with_user(self.alice).action_revoke()
        first = share.sudo().revoked_at
        later = fields.Datetime.now() + timedelta(hours=1)
        with patch("odoo.fields.Datetime.now", return_value=later):
            share.with_user(self.alice).action_revoke()
        self.assertEqual(share.sudo().revoked_at, first)

    def test_shared_with_me_offers_no_recompute(self):
        """Recomputing redoes the reader's own days: not offered beside someone else's."""
        action = self.env.ref("bf_work_hours.action_bf_work_day_shared")
        shared_list = {mode: view_id for view_id, mode in action.views}["list"]
        Day = self.env["bf.work.day"].with_user(self.bob)
        arch = Day.get_views([(shared_list, "list")])["views"]["list"]["arch"]
        self.assertNotIn("action_bf_recompute", arch)
        self.assertIn("action_bf_recompute", Day.get_views([(False, "list")])["views"]["list"]["arch"])

    def test_no_oracle_on_someone_elses_state(self):
        """🔴 A unique index answers before any access check: creating for someone
        else must be refused the same way whether they turned it on (or share) or not."""
        self.activate(self.alice)
        self.Share.with_user(self.alice).create({"viewer_id": self.carol.id})
        for owner in (self.alice, self.carol):  # Alice is active and shares; Carol is neither
            with self.assertRaises(AccessError):
                self.Consent.with_user(self.bob).create({"user_id": owner.id, "scope": "hours"})
            with self.assertRaises(AccessError):
                self.Consent.with_user(self.bob).with_context(default_user_id=owner.id).create({"scope": "hours"})
            with self.assertRaises(AccessError):
                self.Share.with_user(self.bob).create({"owner_id": owner.id, "viewer_id": self.carol.id})
            with self.assertRaises(AccessError):
                self.Share.with_user(self.bob).with_context(default_owner_id=owner.id).create(
                    {"viewer_id": self.carol.id})
