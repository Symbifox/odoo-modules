from datetime import datetime, timedelta
from unittest.mock import patch

from odoo.tests import tagged

from odoo.addons.bf_work_hours.tests.common import WorkHoursCase


@tagged("post_install", "-at_install")
class TestTimerSegments(WorkHoursCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.alice.groups_id = [(4, cls.env.ref("hr_timesheet.group_hr_timesheet_user").id)]
        cls.env["hr.employee"].sudo().create({"name": "WH Alice", "user_id": cls.alice.id})
        project = cls.env["project.project"].sudo().create({"name": "WH project", "allow_timesheets": True})
        cls.task = cls.env["project.task"].sudo().create({"name": "WH task", "project_id": project.id})

    def segments(self):
        return self.Signal.sudo().search([("user_id", "=", self.alice.id), ("source", "=", "timer")])

    def start(self, at):
        with patch("odoo.fields.Datetime.now", return_value=at):
            return self.env["bf.timer"].with_user(self.alice).start_timer(self.task.id)

    def test_pause_and_stop_keep_their_segments(self):
        self.activate(self.alice)
        t0 = datetime.now().replace(microsecond=0) - timedelta(hours=2)
        self.start(t0)
        timer = self.env["bf.timer"].sudo().search([("user_id", "=", self.alice.id)])
        Timer = self.env["bf.timer"].with_user(self.alice)
        with patch("odoo.fields.Datetime.now", return_value=t0 + timedelta(minutes=30)):
            Timer.pause_timer(timer.id)
        with patch("odoo.fields.Datetime.now", return_value=t0 + timedelta(minutes=60)):
            Timer.resume_timer(timer.id)
        with patch("odoo.fields.Datetime.now", return_value=t0 + timedelta(minutes=80)):
            Timer.stop_timer(timer.id)
        spans = sorted((s.date_start, s.date_stop) for s in self.segments())
        self.assertEqual(spans, [(t0, t0 + timedelta(minutes=30)),
                                 (t0 + timedelta(minutes=60), t0 + timedelta(minutes=80))])

    def test_nothing_kept_without_activation(self):
        t0 = datetime.now().replace(microsecond=0) - timedelta(hours=1)
        self.start(t0)
        timer = self.env["bf.timer"].sudo().search([("user_id", "=", self.alice.id)])
        self.env["bf.timer"].with_user(self.alice).stop_timer(timer.id)
        self.assertFalse(self.segments())

    def test_a_running_timer_does_not_count_yet(self):
        """Timers left open for days: only paused or stopped stretches count."""
        self.activate(self.alice)
        t0 = datetime.now().replace(microsecond=0) - timedelta(minutes=40)
        self.start(t0)
        now = datetime.now()
        items = [i for i in self.env["bf.work.collector"]._bf_collect(self.alice, now - timedelta(hours=1), now)
                 if i.source == "timer"]
        self.assertFalse(items)

    def test_an_offline_pause_ends_the_segment_at_the_gesture(self):
        """🟠 Paused on the phone in the morning, synced in the evening: the afternoon is not work."""
        if "paused_at" not in self.env["bf.timer"]._fields:
            self.skipTest("bf_timesheet_timer_mobile is not installed: no offline pause")
        self.activate(self.alice)
        t0 = datetime.now().replace(microsecond=0) - timedelta(hours=10)
        self.start(t0)
        timer = self.env["bf.timer"].sudo().search([("user_id", "=", self.alice.id)])
        Timer = self.env["bf.timer"].with_user(self.alice)
        Timer.pause_timer(timer.id)                    # the sync, now
        timer.with_user(self.alice).write({"paused_at": t0 + timedelta(hours=1)})  # the real moment
        segment = self.segments()
        self.assertEqual(segment.date_stop, t0 + timedelta(hours=1))

    def test_a_forgotten_timer_is_not_kept(self):
        """A timer left running for days is not days of work, nor 12 hours of it."""
        self.activate(self.alice)
        t0 = datetime.now().replace(microsecond=0) - timedelta(days=2)
        self.start(t0)
        timer = self.env["bf.timer"].sudo().search([("user_id", "=", self.alice.id)])
        self.env["bf.timer"].with_user(self.alice).stop_timer(timer.id)
        self.assertFalse(self.segments())


    def test_an_approver_does_not_move_someones_pause(self):
        """🟡 An approver may rewrite a timer: not the person's trace."""
        if "paused_at" not in self.env["bf.timer"]._fields:
            self.skipTest("bf_timesheet_timer_mobile is not installed: no offline pause")
        self.activate(self.alice)
        approver = self.env["res.users"].create({
            "name": "WH approver", "login": "wh_approver",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id,
                                  self.env.ref("hr_timesheet.group_hr_timesheet_approver").id])]})
        t0 = datetime.now().replace(microsecond=0) - timedelta(hours=3)
        self.start(t0)
        timer = self.env["bf.timer"].sudo().search([("user_id", "=", self.alice.id)])
        with patch("odoo.fields.Datetime.now", return_value=t0 + timedelta(hours=2)):
            self.env["bf.timer"].with_user(self.alice).pause_timer(timer.id)
        timer.with_user(approver).write({"paused_at": t0 + timedelta(minutes=5)})
        self.assertEqual(self.segments().date_stop, t0 + timedelta(hours=2))

    def test_a_timer_changed_by_an_approver_no_longer_counts(self):
        """🟠 An approver moves the start of Alice's running timer back: the timer stops counting."""
        self.activate(self.alice)
        approver = self.env["res.users"].create({
            "name": "WH approver 2", "login": "wh_approver_2",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id,
                                  self.env.ref("hr_timesheet.group_hr_timesheet_approver").id])]})
        t0 = datetime.now().replace(microsecond=0) - timedelta(minutes=30)
        self.start(t0)
        timer = self.env["bf.timer"].sudo().search([("user_id", "=", self.alice.id)])
        # Moved back two hours only: still under 12, so the marker is what refuses it.
        timer.with_user(approver).write({"start_time": t0 - timedelta(hours=2)})
        self.assertTrue(timer.bf_work_untrusted)
        now = datetime.now()
        items = [i for i in self.env["bf.work.collector"]._bf_collect(self.alice, now - timedelta(hours=13), now)
                 if i.source == "timer"]
        self.assertFalse(items)
        with patch("odoo.fields.Datetime.now", return_value=now):
            self.env["bf.timer"].with_user(self.alice).pause_timer(timer.id)
        self.assertFalse(self.segments())

    def test_a_timer_made_or_handed_by_someone_else(self):
        """🔴 an approver makes a timer for Alice, or makes his own and hands it over: not hers."""
        self.activate(self.alice)
        approver = self.env["res.users"].create({
            "name": "WH approver 3", "login": "wh_approver_3",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id,
                                  self.env.ref("hr_timesheet.group_hr_timesheet_approver").id])]})
        employee = self.env["hr.employee"].sudo().search([("user_id", "=", self.alice.id)], limit=1)
        t0 = datetime.now().replace(microsecond=0) - timedelta(hours=8)
        made = self.env["bf.timer"].with_user(approver).create({
            "user_id": self.alice.id, "employee_id": employee.id, "project_id": self.task.project_id.id,
            "task_id": self.task.id, "start_time": t0, "first_start": t0, "is_active": True})
        self.assertTrue(made.bf_work_untrusted)
        own = self.env["bf.timer"].with_user(approver).create({
            "user_id": approver.id, "employee_id": employee.id, "project_id": self.task.project_id.id,
            "task_id": self.task.id, "start_time": t0, "first_start": t0, "is_active": True})
        self.assertFalse(own.bf_work_untrusted)
        own.with_user(approver).write({"user_id": self.alice.id})
        self.assertTrue(own.bf_work_untrusted)
        self.assertTrue(own.sudo().copy().bf_work_untrusted, "A copy stays untrusted.")
        # 🟡 Copied by its new owner, without superuser: still untrusted.
        self.assertTrue(own.with_user(self.alice).copy().bf_work_untrusted)
        now = datetime.now()
        items = [i for i in self.env["bf.work.collector"]._bf_collect(self.alice, now - timedelta(hours=10), now)
                 if i.source == "timer"]
        self.assertFalse(items)
