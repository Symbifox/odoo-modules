from datetime import timedelta

from freezegun import freeze_time

from odoo import fields
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestHelpdeskDigest(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True,
                                       mail_notify_force_send=False))
        model = cls.env.ref("helpdesk_mgmt.model_helpdesk_ticket")
        cls.agent = new_test_user(
            cls.env, login="agent-digest", email="agent.digest@example.com",
            groups="base.group_user,helpdesk_mgmt.group_helpdesk_user",
            tz="America/Toronto")
        cls.team = cls.env["helpdesk.ticket.team"].create({
            "name": "Digest", "ack_channel_ids": [(5, 0, 0)], "csat_mode": "none",
            "alias_id": cls.env["mail.alias"].create({
                "alias_name": "digest-essai", "alias_model_id": model.id,
            }).id,
        })
        cls.config = cls.env["daily.digest.config"].create({
            "name": "Essai assistance", "user_ids": [(6, 0, cls.agent.ids)],
            # Pas de météo : l'essai n'appelle aucun service extérieur.
            "include_weather": False, "include_quote": True,
        })
        cls.Item = cls.env["helpdesk.agent.notify.item"]

    def _ticket(self, **vals):
        values = {"name": "Serveur lent", "description": "<p>x</p>",
                  "team_id": self.team.id, "user_id": self.agent.id}
        values.update(vals)
        return self.env["helpdesk.ticket"].create(values)

    def test_section_lists_open_tickets_of_reader(self):
        waiting = self._ticket(name="Attend le client", waiting_state="client")
        other = self._ticket(name="Pas à moi", user_id=False)
        html = self.config._render_helpdesk(self.agent)
        self.assertIn("billet(s) ouvert(s)", html)
        self.assertIn(waiting.number, html)
        self.assertIn("en attente du client", html)
        self.assertNotIn(other.number, html)

    def test_empty_when_nothing(self):
        self.assertEqual(self.config._render_helpdesk(self.agent), "")
        self._ticket()
        self.config.include_helpdesk = False
        self.assertEqual(self.config._render_helpdesk(self.agent), "")

    def test_daily_email_items_go_to_digest_and_are_marked(self):
        ticket = self._ticket()
        item = self.Item.create({
            "user_id": self.agent.id, "ticket_id": ticket.id, "event": "client_reply",
            "channel": "email", "mode": "daily", "summary": "Réponse de Kim : Serveur lent",
        })
        self.assertTrue(self.Item._daily_handled_by_digest(self.agent))
        # bf_helpdesk ne l'envoie pas lui-même : le digest s'en charge.
        with freeze_time(fields.Datetime.now().replace(hour=14)):
            self.Item._cron_send_agent_digests()
        self.assertFalse(item.sent)
        html = self.config._generate_html(self.config._gather_digest_data(self.agent), self.agent)
        self.assertIn("Réponse de Kim", html)
        self.assertFalse(item.sent, "un aperçu ne consomme pas les éléments")
        self.config._send_digest_to(self.agent)
        self.assertTrue(item.sent)

    def test_safety_net_after_26_hours(self):
        ticket = self._ticket()
        item = self.Item.create({
            "user_id": self.agent.id, "ticket_id": ticket.id, "event": "client_reply",
            "channel": "email", "mode": "daily", "summary": "Réponse oubliée",
        })
        later = item.create_date + timedelta(hours=27)
        with freeze_time(later.replace(hour=14)):
            self.Item._cron_send_agent_digests()
        self.assertTrue(item.sent)

    def test_non_recipient_keeps_own_email(self):
        stranger = new_test_user(self.env, login="hors-digest",
                                 groups="base.group_user,helpdesk_mgmt.group_helpdesk_user")
        self.assertFalse(self.Item._daily_handled_by_digest(stranger))
