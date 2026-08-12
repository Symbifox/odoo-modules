from odoo.tests import TransactionCase, tagged


@tagged("bf_helpdesk", "bf_helpdesk_meeting")
class TestTicketMeeting(TransactionCase):
    """Convert ticket → meeting.record."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        cls.Ticket = cls.env["helpdesk.ticket"]
        cls.Meeting = cls.env["meeting.record"]
        cls.alias = cls.env["mail.alias"].create({
            "alias_name": "mt-test",
            "alias_model_id": cls.env.ref("helpdesk_mgmt.model_helpdesk_ticket").id,
        })
        cls.team = cls.env["helpdesk.ticket.team"].create({
            "name": "MT Test Team",
            "alias_id": cls.alias.id,
        })

    def _ticket(self):
        return self.Ticket.create({
            "name": "Meeting from ticket",
            "description": "<p>x</p>",
            "team_id": self.team.id,
        })

    def test_zero_meetings_initially(self):
        ticket = self._ticket()
        self.assertEqual(ticket.meeting_record_count, 0)
        self.assertFalse(ticket.meeting_record_ids)

    def test_create_meeting_action(self):
        ticket = self._ticket()
        action = ticket.action_create_meeting_record()
        self.assertEqual(action["res_model"], "meeting.record")
        meeting = self.Meeting.browse(action["res_id"])
        self.assertTrue(meeting)
        self.assertEqual(meeting.helpdesk_ticket_id, ticket)
        self.assertIn(ticket.number, meeting.name)
        self.assertIn(ticket.name, meeting.name)

    def test_meeting_count_updates(self):
        ticket = self._ticket()
        ticket.action_create_meeting_record()
        ticket.action_create_meeting_record()
        ticket.invalidate_recordset(["meeting_record_count", "meeting_record_ids"])
        self.assertEqual(ticket.meeting_record_count, 2)

    def test_unlink_meeting_clears_link(self):
        ticket = self._ticket()
        ticket.action_create_meeting_record()
        meeting = ticket.meeting_record_ids
        ticket_id = ticket.id
        meeting.unlink()
        ticket.invalidate_recordset(["meeting_record_count", "meeting_record_ids"])
        self.assertEqual(ticket.meeting_record_count, 0)

    def test_view_meeting_records_action(self):
        ticket = self._ticket()
        ticket.action_create_meeting_record()
        action = ticket.action_view_meeting_records()
        self.assertEqual(action["res_model"], "meeting.record")
        self.assertEqual(action["domain"], [("helpdesk_ticket_id", "=", ticket.id)])
