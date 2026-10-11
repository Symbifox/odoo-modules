from datetime import date

from odoo.tests import tagged

from odoo.addons.bf_work_hours.tests.common import WorkHoursCase


@tagged("post_install", "-at_install")
class TestTwoNumbers(WorkHoursCase):

    def test_entered_hours_beside_reconstructed_hours(self):
        employee = self.env["hr.employee"].sudo().create({"name": "WH Alice", "user_id": self.alice.id})
        project = self.env["project.project"].sudo().create({"name": "WH project", "allow_timesheets": True})
        day = date.today()
        Line = self.env["account.analytic.line"].sudo()
        Line.create({"name": "a", "project_id": project.id, "employee_id": employee.id, "date": day, "unit_amount": 6})
        Line.create({"name": "b", "project_id": project.id, "employee_id": employee.id, "date": day, "unit_amount": 5})
        self.activate(self.alice, granted_at="2020-01-01 00:00:00")
        record = self.Day._bf_compute(self.alice, [day])
        self.assertAlmostEqual(record.timesheet_hours, 11.0)
        self.assertFalse(record.hours, "No trace: the entered hours never fill the reconstructed ones.")
