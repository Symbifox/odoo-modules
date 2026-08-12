from unittest.mock import patch

from odoo.tests import TransactionCase, tagged


@tagged("bf_helpdesk", "bf_helpdesk_hour_bank")
class TestTicketHourBank(TransactionCase):
    """Hour bank link mirrors team, balance + low-flag computed correctly."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        cls.Team = cls.env["helpdesk.ticket.team"]
        cls.Ticket = cls.env["helpdesk.ticket"]
        cls.HourBank = cls.env["hour.bank.client"]
        cls.partner = cls.env["res.partner"].create({
            "name": "Test Hour Bank Client",
            "email": "hb@test.invalid",
        })
        cls.alias = cls.env["mail.alias"].create({
            "alias_name": "test-hb-team",
            "alias_model_id": cls.env.ref("helpdesk_mgmt.model_helpdesk_ticket").id,
        })
        cls.bank = cls.HourBank.create({
            "partner_id": cls.partner.id,
        })
        cls.team = cls.Team.create({
            "name": "Hour Bank Test Team",
            "alias_id": cls.alias.id,
            "hour_bank_id": cls.bank.id,
            "hour_bank_alert_threshold_hours": 5.0,
        })

    def _make_ticket(self):
        return self.Ticket.create({
            "name": "Test ticket",
            "description": "<p>desc</p>",
            "team_id": self.team.id,
        })

    def test_hour_bank_mirrors_team(self):
        ticket = self._make_ticket()
        self.assertEqual(ticket.hour_bank_id, self.bank)

    def test_hour_bank_none_without_team_link(self):
        team_no_bank = self.Team.create({
            "name": "Team No Bank",
            "alias_id": self.env["mail.alias"].create({
                "alias_name": "team-no-bank",
                "alias_model_id": self.env.ref("helpdesk_mgmt.model_helpdesk_ticket").id,
            }).id,
        })
        ticket = self.Ticket.create({
            "name": "No bank ticket",
            "description": "<p>x</p>",
            "team_id": team_no_bank.id,
        })
        self.assertFalse(ticket.hour_bank_id)
        self.assertEqual(ticket.hour_bank_balance, 0.0)
        self.assertFalse(ticket.hour_bank_low)

    def test_hour_bank_balance_high_no_alert(self):
        ticket = self._make_ticket()
        with patch.object(
            type(self.bank), "_compute_balance",
            lambda recs: [setattr(r, "current_balance", 50.0) for r in recs],
        ):
            self.bank.invalidate_recordset(["current_balance"])
            ticket.invalidate_recordset(["hour_bank_balance", "hour_bank_low"])
            self.assertEqual(ticket.hour_bank_balance, 50.0)
            self.assertFalse(ticket.hour_bank_low,
                             "50h > 5h threshold → low must be False")

    def test_hour_bank_balance_low_alert(self):
        ticket = self._make_ticket()
        with patch.object(
            type(self.bank), "_compute_balance",
            lambda recs: [setattr(r, "current_balance", 2.0) for r in recs],
        ):
            self.bank.invalidate_recordset(["current_balance"])
            ticket.invalidate_recordset(["hour_bank_balance", "hour_bank_low"])
            self.assertEqual(ticket.hour_bank_balance, 2.0)
            self.assertTrue(ticket.hour_bank_low,
                            "2h <= 5h threshold → low must be True")
