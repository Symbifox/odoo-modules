from odoo import fields
from odoo.tests import tagged

from odoo.addons.bf_work_category.tests.common import WorkCategoryCase


@tagged("post_install", "-at_install")
class TestMeetingCategory(WorkCategoryCase):

    def test_meeting_follows_its_project(self):
        meeting = self.env["meeting.record"].create({
            "name": "WC", "date": fields.Datetime.now(), "project_id": self.project_client.id})
        self.assertEqual(self.stored(meeting), (self.client.id, "project"))
        self.project_client.tag_ids = [(6, 0, self.bizdev.ids)]
        self.assertEqual(self.stored(meeting), (self.bizdev.id, "project"))

    def test_project_changes_keep_last_modified(self):
        meeting = self.env["meeting.record"].create({
            "name": "WC", "date": fields.Datetime.now(), "project_id": self.project_client.id})
        self.age(meeting)
        self.project_client.tag_ids = [(6, 0, self.bizdev.ids)]
        self.assertEqual(self.stored(meeting), (self.bizdev.id, "project"))
        self.assertEqual(self.last_modified(meeting), (self.OLD, 1))
