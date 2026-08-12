from odoo.tests import TransactionCase, tagged


@tagged("bf_helpdesk", "bf_helpdesk_csat")
class TestTicketCsat(TransactionCase):
    """CSAT survey auto-sent when ticket transitions to a closed stage."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        cls.Ticket = cls.env["helpdesk.ticket"]
        cls.Stage = cls.env["helpdesk.ticket.stage"]
        cls.Survey = cls.env["survey.survey"]
        cls.UserInput = cls.env["survey.user_input"]
        cls.alias = cls.env["mail.alias"].create({
            "alias_name": "csat-test",
            "alias_model_id": cls.env.ref("helpdesk_mgmt.model_helpdesk_ticket").id,
        })
        cls.survey = cls.Survey.create({
            "title": "CSAT Test",
            "access_mode": "public",
            "users_login_required": False,
            "questions_layout": "page_per_section",
        })
        cls.team = cls.env["helpdesk.ticket.team"].create({
            "name": "CSAT Team",
            "alias_id": cls.alias.id,
            "csat_survey_id": cls.survey.id,
        })
        cls.partner = cls.env["res.partner"].create({
            "name": "CSAT Partner",
            "email": "csat@test.invalid",
        })
        cls.open_stage = cls.Stage.search([("closed", "=", False)], limit=1)
        cls.closed_stage = cls.Stage.search([("closed", "=", True)], limit=1)

    def _ticket(self):
        return self.Ticket.create({
            "name": "CSAT test",
            "description": "<p>x</p>",
            "team_id": self.team.id,
            "partner_id": self.partner.id,
            "partner_email": self.partner.email,
            "stage_id": self.open_stage.id,
        })

    def test_no_csat_when_team_has_no_survey(self):
        team_off = self.env["helpdesk.ticket.team"].create({
            "name": "No Survey",
            "alias_id": self.env["mail.alias"].create({
                "alias_name": "no-survey",
                "alias_model_id": self.env.ref("helpdesk_mgmt.model_helpdesk_ticket").id,
            }).id,
        })
        ticket = self.Ticket.create({
            "name": "no csat",
            "description": "<p>x</p>",
            "team_id": team_off.id,
            "partner_id": self.partner.id,
            "partner_email": self.partner.email,
            "stage_id": self.open_stage.id,
        })
        ticket.stage_id = self.closed_stage.id
        self.assertFalse(ticket.csat_user_input_id)

    def test_csat_sent_on_close(self):
        ticket = self._ticket()
        self.assertFalse(ticket.csat_user_input_id)
        ticket.stage_id = self.closed_stage.id
        self.assertTrue(ticket.csat_user_input_id)
        self.assertEqual(ticket.csat_user_input_id.survey_id, self.survey)
        self.assertEqual(ticket.csat_user_input_id.email, self.partner.email)

    def test_csat_not_resent_on_re_close(self):
        ticket = self._ticket()
        ticket.stage_id = self.closed_stage.id
        first = ticket.csat_user_input_id
        # Move back open then close again
        ticket.stage_id = self.open_stage.id
        ticket.stage_id = self.closed_stage.id
        self.assertEqual(ticket.csat_user_input_id, first,
                         "CSAT must not be re-sent if a user_input already exists")

    def test_no_csat_when_partner_email_empty(self):
        ticket = self.Ticket.create({
            "name": "no email",
            "description": "<p>x</p>",
            "team_id": self.team.id,
            "stage_id": self.open_stage.id,
        })
        ticket.stage_id = self.closed_stage.id
        self.assertFalse(ticket.csat_user_input_id)

    def test_csat_state_mirrors_user_input(self):
        ticket = self._ticket()
        ticket.stage_id = self.closed_stage.id
        self.assertEqual(ticket.csat_state, ticket.csat_user_input_id.state)
