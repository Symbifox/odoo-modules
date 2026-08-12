from odoo.exceptions import UserError, ValidationError
from odoo.tests import TransactionCase, tagged


@tagged("bf_helpdesk", "bf_helpdesk_timesheet")
class TestTicketTimesheet(TransactionCase):
    """Direct time entry on tickets: lines land on the ticket's project so
    they feed the team hour bank; total_hours reflects logged time."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        cls.Team = cls.env["helpdesk.ticket.team"]
        cls.Ticket = cls.env["helpdesk.ticket"]
        cls.HourBank = cls.env["hour.bank.client"]
        cls.Project = cls.env["project.project"]

        cls.partner = cls.env["res.partner"].create({
            "name": "Test Timesheet Client",
            "email": "ts@test.invalid",
        })
        # Employee for the connected user so time can be logged for them.
        cls.employee = cls.env["hr.employee"].create({
            "name": "Test Timesheet Employee",
            "user_id": cls.env.user.id,
            "company_id": cls.env.company.id,
        })
        cls.project = cls.Project.create({
            "name": "Test HB Project",
            "allow_timesheets": True,
        })
        cls.bank = cls.HourBank.create({
            "partner_id": cls.partner.id,
            "project_ids": [(6, 0, cls.project.ids)],
        })
        cls.team = cls.Team.create({
            "name": "Timesheet Test Team",
            "alias_id": cls.env["mail.alias"].create({
                "alias_name": "test-ts-team",
                "alias_model_id": cls.env.ref(
                    "helpdesk_mgmt.model_helpdesk_ticket").id,
            }).id,
            "hour_bank_id": cls.bank.id,
        })

    def _make_ticket(self, project=None):
        vals = {
            "name": "Timesheet ticket",
            "description": "<p>desc</p>",
            "team_id": self.team.id,
        }
        if project is not None:
            vals["project_id"] = project.id
        return self.Ticket.create(vals)

    def test_log_time_creates_line_and_totals(self):
        ticket = self._make_ticket(project=self.project)
        res = ticket.action_bf_create_chatter_timesheet(1.5, "<p>Investigation</p>")
        self.assertIn("id", res)
        line = self.env["account.analytic.line"].browse(res["id"])
        self.assertEqual(line.ticket_id, ticket)
        self.assertEqual(line.project_id, self.project)
        self.assertEqual(line.unit_amount, 1.5)
        self.assertEqual(line.name, "Investigation")
        ticket.invalidate_recordset(["total_hours", "timesheet_ids"])
        self.assertEqual(ticket.total_hours, 1.5)
        self.assertIn(line, ticket.timesheet_ids)

    def test_log_time_falls_back_to_hour_bank_project(self):
        # No project on the ticket → falls back to the team hour bank project.
        ticket = self._make_ticket()
        ticket.project_id = False
        res = ticket.action_bf_create_chatter_timesheet(0.25, "")
        line = self.env["account.analytic.line"].browse(res["id"])
        self.assertEqual(line.project_id, self.project)
        # Empty body → description falls back to the ticket name.
        self.assertEqual(line.name, ticket.name)

    def test_log_time_requires_project(self):
        team_no_project = self.Team.create({
            "name": "No Project Team",
            "alias_id": self.env["mail.alias"].create({
                "alias_name": "test-ts-noproj",
                "alias_model_id": self.env.ref(
                    "helpdesk_mgmt.model_helpdesk_ticket").id,
            }).id,
        })
        ticket = self.Ticket.create({
            "name": "No project ticket",
            "team_id": team_no_project.id,
        })
        ticket.project_id = False
        with self.assertRaises(UserError):
            ticket.action_bf_create_chatter_timesheet(1.0, "x")

    def test_log_time_zero_raises(self):
        ticket = self._make_ticket(project=self.project)
        with self.assertRaises(ValidationError):
            ticket.action_bf_create_chatter_timesheet(0, "x")
