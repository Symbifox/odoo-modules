from datetime import datetime, timedelta

from odoo import SUPERUSER_ID
from odoo.tests import tagged

from odoo.addons.bf_work_hours.tests.common import WorkHoursCase


@tagged("post_install", "-at_install")
class TestMeetingTraces(WorkHoursCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.processing = cls.env["res.users"].create({
            "name": "WH processing", "login": "wh_processing@example.com",
            "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])]})
        cls.env["ir.config_parameter"].sudo().set_param(
            "bf_work_hours_meeting.processing_logins", "wh_processing@example.com")

    def record(self, author, attendance=(), organizer=False, minutes=60):
        return self.env["meeting.record"].with_user(author).sudo().create({
            "name": "WH meeting", "date": datetime.now() - timedelta(hours=2), "duration_minutes": minutes,
            "organizer_id": organizer and organizer.id,
            "attendance_ids": [(0, 0, {"partner_id": p.id, "status": s}) for p, s in attendance],
        })

    def meeting_items(self, user):
        now = datetime.now()
        return [i for i in self.env["bf.work.collector"]._bf_collect(user, now - timedelta(hours=4), now)
                if i.source == "meeting_report"]

    def test_present_by_the_processing_counts_absent_does_not(self):
        self.record(self.processing, [(self.alice.partner_id, "present"), (self.bob.partner_id, "absent")])
        items = self.meeting_items(self.alice)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].kind, "meeting")
        self.assertEqual(items[0].stop - items[0].start, timedelta(hours=1))
        self.assertFalse(self.meeting_items(self.bob))

    def test_a_colleague_cannot_mark_someone_present(self):
        """🟠 Bob writes a report with Alice present, or with Alice as organizer: it does not count for her."""
        self.record(self.bob, [(self.alice.partner_id, "present"), (self.bob.partner_id, "present")])
        self.record(self.bob, organizer=self.alice)
        self.assertFalse(self.meeting_items(self.alice))
        self.assertEqual(len(self.meeting_items(self.bob)), 1, "Bob's own report counts for Bob.")

    def test_a_report_without_attendance_counts_for_nobody(self):
        """🟠 no presence, no meeting, whoever made the report and whoever stretches it."""
        record = self.record(self.processing, organizer=self.alice)
        self.assertFalse(self.meeting_items(self.alice))
        record.with_user(self.bob).sudo().write({"duration_minutes": 470})
        self.assertFalse(self.meeting_items(self.alice))

    def test_the_processing_refines_the_length(self):
        """The processing shortens the report it made: the presence follows; Bob stretching it does not."""
        record = self.record(self.processing, [(self.alice.partner_id, "present")], minutes=90)
        record.with_user(self.processing).sudo().write({"duration_minutes": 45})
        record.with_user(self.bob).sudo().write({"duration_minutes": 420})
        items = self.meeting_items(self.alice)
        self.assertEqual(items[0].stop - items[0].start, timedelta(minutes=45))

    def test_a_presence_from_before_the_installation(self):
        record = self.record(self.processing, [(self.alice.partner_id, "present")])
        line = record.attendance_ids
        self.env.flush_all()
        self.env.cr.execute("UPDATE meeting_attendance SET bf_work_status_by = NULL WHERE id = %s", [line.id])
        self.env.invalidate_all()
        self.assertFalse(self.meeting_items(self.alice), "Unstamped: counts for nothing.")
        self.env["meeting.attendance"]._bf_work_stamp_existing()
        self.assertEqual(line.bf_work_status_by, "trusted")
        self.assertEqual(len(self.meeting_items(self.alice)), 1)

    def test_absent_line_of_one_and_present_line_of_another(self):
        self.record(self.processing, [(self.alice.partner_id, "absent"), (self.bob.partner_id, "present")])
        self.assertFalse(self.meeting_items(self.alice))

    def test_a_colleague_marking_someone_present_on_her_own_report(self):
        """🟠 Alice made the report and was absent; Bob marks her present: not hers."""
        record = self.record(self.alice, [(self.alice.partner_id, "absent")])
        record.attendance_ids.with_user(self.bob).sudo().write({"status": "present"})
        self.assertEqual(record.attendance_ids.bf_work_status_by, "other")
        self.assertFalse(self.meeting_items(self.alice))

    def test_a_meeting_stretched_afterwards(self):
        """🟠 Noted present for 30 minutes; Bob stretches the meeting to 7 hours: 30 minutes count."""
        record = self.record(self.processing, [(self.alice.partner_id, "present")], minutes=30)
        record.with_user(self.bob).sudo().write({"duration_minutes": 420})
        items = self.meeting_items(self.alice)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].stop - items[0].start, timedelta(minutes=30))

    def test_the_stamp_is_not_writable(self):
        record = self.record(self.bob, [(self.alice.partner_id, "present")])
        record.attendance_ids.with_user(self.bob).sudo().write({"bf_work_status_by": "trusted"})
        self.assertEqual(record.attendance_ids.bf_work_status_by, "other")

    def test_a_presence_handed_to_someone_else(self):
        """🟠 Bob's own presence, noted by the processing, moved by Bob to Alice: not hers."""
        record = self.record(self.processing, [(self.bob.partner_id, "present")])
        record.attendance_ids.with_user(self.bob).sudo().write({"partner_id": self.alice.partner_id.id})
        self.assertEqual(record.attendance_ids.bf_work_status_by, "other")
        self.assertFalse(self.meeting_items(self.alice))

    def test_an_archived_report_is_not_a_meeting(self):
        record = self.record(self.processing, [(self.alice.partner_id, "present")])
        record.sudo().active = False
        self.assertFalse(self.meeting_items(self.alice))

    def test_a_peers_report_received_as_superuser(self):
        """🟠 A federation inbox serves a peer's report as superuser, inside a request: someone else."""
        from odoo.addons.bf_work_hours.tests.common import acting_as
        with acting_as(SUPERUSER_ID, "/bf_federation/inbox"):
            record = self.env["meeting.record"].sudo().create({
                "name": "WH peer", "date": datetime.now() - timedelta(hours=2), "duration_minutes": 60,
                "attendance_ids": [(0, 0, {"partner_id": self.alice.partner_id.id, "status": "present"})]})
        self.assertEqual(record.attendance_ids.bf_work_status_by, "other")
        self.assertFalse(self.meeting_items(self.alice))

    def test_naming_the_processing_after_the_installation(self):
        """🟠 The parameter forgotten at installation: setting it later trusts what the processing wrote."""
        self.env["ir.config_parameter"].sudo().set_param("bf_work_hours_meeting.processing_logins", "")
        record = self.record(self.processing, [(self.alice.partner_id, "present")])
        self.assertEqual(record.attendance_ids.bf_work_status_by, "other")
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_work_hours_meeting.processing_logins", "wh_processing@example.com")
        self.assertEqual(record.attendance_ids.bf_work_status_by, "trusted")
        self.assertEqual(len(self.meeting_items(self.alice)), 1)

    def test_a_presence_noted_under_an_impersonation(self):
        """🟡 Noted while impersonating the processing account: naming it later does not turn it trusted."""
        from odoo.addons.bf_work_hours.tests.common import acting_as
        self.env["ir.config_parameter"].sudo().set_param("bf_work_hours_meeting.processing_logins", "")
        with acting_as(self.processing.id, bf_impersonate={"from_uid": self.bob.id}):
            record = self.record(self.processing, [(self.alice.partner_id, "present")])
        self.assertEqual(record.attendance_ids.bf_work_status_by, "other")
        self.assertFalse(record.attendance_ids.bf_work_status_uid)
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_work_hours_meeting.processing_logins", "wh_processing@example.com")
        self.assertEqual(record.attendance_ids.bf_work_status_by, "other")
