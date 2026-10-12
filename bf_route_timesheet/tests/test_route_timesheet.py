from freezegun import freeze_time

from odoo.exceptions import AccessError, ValidationError
from odoo.tests import tagged

from odoo.addons.bf_route.tests.common import MONDAY, RouteCase


@tagged("post_install", "-at_install")
class TestRouteTimesheet(RouteCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.employee = cls.env["hr.employee"].create({
            "name": "Route Worker", "user_id": cls.worker.id, "company_id": cls.company.id})
        cls.project = cls.env["project.project"].create({
            "name": "Deliveries", "allow_timesheets": True, "company_id": cls.company.id})
        cls.task = cls.env["project.task"].create({"name": "North shore", "project_id": cls.project.id})
        cls.default_project = cls.env["project.project"].create({
            "name": "Road time", "allow_timesheets": True, "company_id": cls.company.id})
        cls.company.bf_route_timesheet_project_id = False

    def finish(self, end="2026-10-12 19:30:00", client_start=None, **context):
        day = self.make_day()
        with freeze_time("2026-10-12 12:00:00"):
            self.start(day, client_time=client_start)
            day.stop_ids.sorted("sequence")[0].with_user(self.worker).app_mark("done", app_key="a")
        with freeze_time(end):
            day.with_user(self.worker).with_context(**context).app_finish()
        return day

    def test_a_day_left_open_overnight_is_not_logged(self):
        self.route.sudo().timesheet_project_id = self.project
        day = self.finish(end="2026-10-13 11:30:00")
        self.assertEqual(day.state, "done")
        self.assertFalse(day.sudo().timesheet_ids)
        self.assertIn("more than 14", day.message_ids.filtered(lambda m: "more than 14" in (m.body or ""))[:1].body)
        activity = day.activity_ids.filtered(lambda a: "Time not logged" in (a.summary or ""))
        self.assertEqual(activity.user_id, self.manager)

    def test_the_caller_s_context_never_silences_the_office(self):
        # action_finish is public: the worker can call it over RPC with any context.
        self.route.sudo().timesheet_project_id = self.project
        day = self.make_day()
        with freeze_time("2026-10-12 12:00:00"):
            self.start(day)
        with freeze_time("2026-10-13 11:30:00"):
            day.with_user(self.worker).with_context(mail_activity_quick_update=True).action_finish()
        self.assertFalse(day.sudo().timesheet_ids)
        notified = self.env["mail.message"].search_count([
            ("model", "=", "bf.route.day"), ("res_id", "=", day.id), ("message_type", "=", "user_notification"),
            ("body", "ilike", "Time not logged")])
        self.assertEqual(notified, 1, "the person responsible is told, whatever the caller's context")

    def test_a_start_sent_late_is_not_trusted_for_the_hours(self):
        self.route.sudo().timesheet_project_id = self.project
        day = self.finish(end="2026-10-12 17:00:00", client_start="2026-10-12T05:00:00Z")
        self.assertEqual(day.date_started.hour, 5, "the phone's time is kept for the day")
        self.assertFalse(day.sudo().timesheet_ids)
        self.assertIn("reached the server 420 min", day.message_ids[0].body)

    def test_days_on_the_road_at_install_keep_their_hours(self):
        from odoo.addons.bf_route_timesheet import post_init_hook
        day = self.make_day()
        self.start(day)
        day.sudo().start_received_at = False
        day.flush_recordset()
        post_init_hook(self.env)
        day.invalidate_recordset(["start_received_at"])
        self.assertEqual(day.start_received_at, day.date_started)

    def test_a_manager_without_project_rights_duplicates_a_route(self):
        self.route.sudo().timesheet_project_id = self.project
        copy = self.route.with_user(self.manager).copy()
        self.assertFalse(copy.sudo().timesheet_project_id)

    def test_the_project_is_guarded_on_the_server(self):
        with self.assertRaises(AccessError):
            self.route.with_user(self.manager).write({"timesheet_project_id": self.project.id})
        other_task = self.env["project.task"].create({"name": "Elsewhere", "project_id": self.default_project.id})
        with self.assertRaises(ValidationError):
            self.route.sudo().write({"timesheet_project_id": self.project.id, "timesheet_task_id": other_task.id})

    def test_day_logs_its_time_on_the_route_s_project(self):
        self.route.sudo().write({"timesheet_project_id": self.project.id, "timesheet_task_id": self.task.id})
        day = self.finish()
        line = day.sudo().timesheet_ids
        self.assertEqual(len(line), 1)
        self.assertEqual(line.employee_id, self.employee)
        self.assertEqual(line.project_id, self.project)
        self.assertEqual(line.task_id, self.task)
        self.assertEqual(line.date, MONDAY)
        self.assertAlmostEqual(line.unit_amount, 7.5)
        self.assertIn("1 of 3 stops", line.name)
        self.assertAlmostEqual(day.timesheet_hours, line.unit_amount)
        self.assertIn("logged for Route Worker", day.message_ids[0].body)

    def test_default_project_of_the_settings(self):
        self.company.bf_route_timesheet_project_id = self.default_project
        day = self.finish()
        self.assertEqual(day.sudo().timesheet_ids.project_id, self.default_project)
        self.assertFalse(day.sudo().timesheet_ids.task_id)

    def test_no_project_no_time(self):
        day = self.finish()
        self.assertEqual(day.state, "done")
        self.assertFalse(day.sudo().timesheet_ids)
        self.assertFalse(day.message_ids.filtered(lambda m: "not logged" in (m.body or "")),
                         "no project is a choice, not a failure")

    def test_worker_without_employee_still_finishes(self):
        self.route.sudo().timesheet_project_id = self.project
        self.employee.user_id = False
        day = self.finish()
        self.assertEqual(day.state, "done")
        self.assertFalse(day.sudo().timesheet_ids)
        self.assertIn("has no employee in this company", day.message_ids[0].body)

    def test_the_worker_sees_their_time(self):
        self.route.sudo().timesheet_project_id = self.project
        day = self.finish()
        self.worker.groups_id += self.env.ref("hr_timesheet.group_hr_timesheet_user")
        self.assertEqual(self.env["account.analytic.line"].with_user(self.worker).search(
            [("bf_route_day_id", "=", day.id)]), day.sudo().timesheet_ids)

    def test_a_manager_without_time_sheet_rights_opens_the_day(self):
        self.route.sudo().timesheet_project_id = self.project
        day = self.finish()
        form = self.env["bf.route.day"].with_user(self.manager).get_views([(False, "form")])
        arch = form["views"]["form"]["arch"]
        self.assertNotIn("action_open_timesheets", arch)
        day.with_user(self.manager).read(["timesheet_hours", "name", "state"])

    def test_nobody_ties_a_time_sheet_to_a_day_by_hand(self):
        self.route.sudo().timesheet_project_id = self.project
        day = self.finish()
        self.manager.groups_id += self.env.ref("hr_timesheet.group_hr_timesheet_approver")
        with self.assertRaises(AccessError):
            day.sudo().timesheet_ids.with_user(self.manager).write({"bf_route_day_id": False})
        self.worker.groups_id += self.env.ref("hr_timesheet.group_hr_timesheet_user")
        with self.assertRaises(AccessError):
            self.env["account.analytic.line"].with_user(self.worker).create({
                "name": "Forged", "project_id": self.project.id, "unit_amount": 1,
                "employee_id": self.employee.id, "bf_route_day_id": day.id})
