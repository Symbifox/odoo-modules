from datetime import datetime, timedelta

from odoo.tests import tagged

from odoo.addons.bf_work_hours.tests.common import WorkHoursCase


@tagged("post_install", "-at_install")
class TestNoteTraces(WorkHoursCase):

    def note_items(self, user):
        now = datetime.now()
        return [i for i in self.env["bf.work.collector"]._bf_collect(user, now - timedelta(hours=1),
                                                                     now + timedelta(hours=1))
                if i.source == "note"]

    def test_a_note_written_is_a_trace(self):
        self.env["bf.note"].with_user(self.alice).create({"name": "WH", "body": "x"})
        self.assertEqual(len(self.note_items(self.alice)), 1)
        self.assertFalse(self.note_items(self.bob))

    def test_an_archived_note_still_counts(self):
        note = self.env["bf.note"].with_user(self.alice).create({"name": "WH", "body": "x"})
        note.active = False
        self.assertEqual(len(self.note_items(self.alice)), 1)
