from odoo.tests import tagged

from odoo.addons.bf_work_category.tests.common import WorkCategoryCase


@tagged("post_install", "-at_install")
class TestTimesheetCategory(WorkCategoryCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.employee = cls.env["hr.employee"].create({"name": "WC employee"})
        cls.task = cls.env["project.task"].create({"name": "t", "project_id": cls.project_bd.id})

    def line(self, **vals):
        return self.env["account.analytic.line"].create(
            {"name": "work", "unit_amount": 1.0, "employee_id": self.employee.id, **vals})

    def test_line_follows_its_task(self):
        line = self.line(task_id=self.task.id)
        self.assertEqual(self.stored(line), (self.bizdev.id, "task"))

    def test_line_without_task_follows_its_project(self):
        line = self.line(project_id=self.project_client.id)
        self.assertEqual(self.stored(line), (self.client.id, "project"))

    def test_task_label_reaches_its_lines(self):
        line = self.line(task_id=self.task.id)
        self.task.tag_ids = [(6, 0, self.client.ids)]
        self.assertEqual(self.stored(line), (self.client.id, "task"))

    def test_override_on_a_line(self):
        line = self.line(task_id=self.task.id, work_category_manual_id=self.client.id)
        self.assertEqual(self.stored(line), (self.client.id, "manual"))

    def test_hours_grouped_by_category(self):
        self.line(task_id=self.task.id, unit_amount=2.0)
        self.line(project_id=self.project_bd.id, unit_amount=0.5)
        self.line(project_id=self.project_client.id, unit_amount=1.5)
        groups = self.env["account.analytic.line"].read_group(
            [("employee_id", "=", self.employee.id)], ["unit_amount:sum"], ["work_category_id"])
        hours = {group["work_category_id"][0]: group["unit_amount"] for group in groups}
        self.assertEqual(hours, {self.bizdev.id: 2.5, self.client.id: 1.5})

    def test_task_changes_keep_last_modified(self):
        line = self.line(task_id=self.task.id)
        self.age(line)
        self.task.tag_ids = [(6, 0, self.client.ids)]
        self.assertEqual(self.stored(line), (self.client.id, "task"))
        self.assertEqual(self.last_modified(line), (self.OLD, 1))

    def test_timesheet_analysis_by_category(self):
        # The analysis report inherits the timesheet search view (and its
        # category filter): its views must load and group by category.
        self.line(task_id=self.task.id, unit_amount=3.0)
        self.env.flush_all()
        Report = self.env["timesheets.analysis.report"]
        Report.init()
        self.assertIn("work_category_id", Report.fields_get())
        self.assertIn("work_category_id", Report.get_view(view_type="search")["arch"])
        for action in ("hr_timesheet.timesheet_action_report_by_employee", "hr_timesheet.timesheet_action_report_by_project",
                       "hr_timesheet.timesheet_action_report_by_task"):
            act = self.env.ref(action, raise_if_not_found=False)
            if act:
                Report.get_views([(False, "pivot"), (False, "graph"), (act.search_view_id.id or False, "search")])
        groups = Report.read_group([("task_id", "=", self.task.id)], ["unit_amount:sum"], ["work_category_id"])
        self.assertEqual([(g["work_category_id"][0], g["unit_amount"]) for g in groups], [(self.bizdev.id, 3.0)])
