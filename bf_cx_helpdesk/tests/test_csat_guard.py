"""Le garde-fou de sollicitation doit jouer sur le sondage de bf_helpdesk.

Ce pont ne dépend pas de bf_helpdesk : selon l'ordre de chargement, sa
surcharge de _send_csat_invite se retrouve au-dessus ou au-dessous de celle
de bf_helpdesk. Avant 18.0.1.1.1, au-dessous, elle rendait la main sans rien
envoyer.
"""

from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestCsatGuard(TransactionCase):

    def setUp(self):
        super().setUp()
        if "helpdesk.ticket.csat" not in self.env:
            self.skipTest("bf_helpdesk (sondage natif) absent")

    def test_cooldown_blocks_second_survey(self):
        model = self.env.ref("helpdesk_mgmt.model_helpdesk_ticket")
        team = self.env["helpdesk.ticket.team"].create({
            "name": "Garde CX", "csat_mode": "native", "csat_delay_hours": 0,
            "ack_channel_ids": [(5, 0, 0)],
            "alias_id": self.env["mail.alias"].create({
                "alias_name": "garde-cx", "alias_model_id": model.id,
            }).id,
        })
        stages = team._get_applicable_stages()
        opened = stages.filtered(lambda s: not s.closed)[:1]
        closed = stages.filtered("closed")[:1]
        partner = self.env["res.partner"].create({
            "name": "Client garde", "email": "client.garde@example.com",
        })
        states = []
        for i in range(2):
            ticket = self.env["helpdesk.ticket"].create({
                "name": f"Garde {i}", "description": "<p>x</p>",
                "team_id": team.id, "stage_id": opened.id,
                "partner_id": partner.id, "partner_email": partner.email,
            })
            ticket.stage_id = closed
            states.append(ticket.csat_ids.state)
        self.assertEqual(states, ["sent", "cancelled"])
        self.assertTrue(partner.bf_cx_last_solicited)
