from datetime import timedelta

from freezegun import freeze_time

from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged("bf_helpdesk", "bf_helpdesk_agent_notify")
class TestAgentNotify(TransactionCase):
    """Matrice événement × canal × mode des agents (18.0.4.9.0)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["res.lang"]._activate_lang("fr_CA")
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True,
                                       mail_notify_force_send=False, lang="fr_CA"))
        cls.env.company.partner_id.lang = "fr_CA"
        cls.Ticket = cls.env["helpdesk.ticket"]
        cls.Pref = cls.env["helpdesk.notify.pref"]
        cls.Item = cls.env["helpdesk.agent.notify.item"]
        model = cls.env.ref("helpdesk_mgmt.model_helpdesk_ticket")
        cls.agent = new_test_user(
            cls.env, login="agent-matrice", email="agent.matrice@example.com",
            groups="base.group_user,helpdesk_mgmt.group_helpdesk_user",
            notification_type="email", tz="America/Toronto")
        cls.other = new_test_user(
            cls.env, login="autre-agent", email="autre@example.com",
            groups="base.group_user,helpdesk_mgmt.group_helpdesk_user")
        cls.team = cls.env["helpdesk.ticket.team"].create({
            "name": "Matrice", "ack_channel_ids": [(5, 0, 0)], "csat_mode": "none",
            "user_ids": [(6, 0, cls.agent.ids)],
            "alias_id": cls.env["mail.alias"].create({
                "alias_name": "matrice", "alias_model_id": model.id,
            }).id,
        })
        cls.client = cls.env["res.partner"].create({
            "name": "Client Matrice", "email": "client.matrice@example.com", "lang": "fr_CA"})

    def _ticket(self, **vals):
        values = {"name": "Réseau lent", "description": "<p>x</p>",
                  "team_id": self.team.id, "partner_id": self.client.id}
        values.update(vals)
        ticket = self.Ticket.create(values)
        ticket.message_subscribe(partner_ids=self.agent.partner_id.ids)
        return ticket

    def _client_says(self, ticket, body="Toujours lent."):
        return ticket.message_post(body=body, author_id=self.client.id,
                                   message_type="email", subtype_xmlid="mail.mt_comment")

    def _notifs(self, user, kind=None):
        domain = [("res_partner_id", "=", user.partner_id.id)]
        if kind:
            domain.append(("notification_type", "=", kind))
        return self.env["mail.notification"].search(domain)

    def _pref(self, event, channel="odoo", mode="immediate", user=None):
        return self.Pref.create({"user_id": (user or self.agent).id, "event": event,
                                 "channel": channel, "mode": mode})

    # ------------------------------------------------------------ par défaut

    def test_without_pref_nothing_changes(self):
        ticket = self._ticket()
        msg = self._client_says(ticket)
        notif = self._notifs(self.agent).filtered(lambda n: n.mail_message_id == msg)
        self.assertEqual(notif.notification_type, "email")
        self.assertFalse(self.Item.search([]))

    # ------------------------------------------------------------ immédiat

    def test_odoo_channel_goes_to_inbox_even_for_email_user(self):
        self._pref("client_reply", "odoo")
        ticket = self._ticket()
        msg = self._client_says(ticket)
        self.assertFalse(self._notifs(self.agent).filtered(lambda n: n.mail_message_id == msg))
        inbox = self._notifs(self.agent, "inbox").mail_message_id.filtered(
            lambda m: m.model == "helpdesk.ticket" and m.res_id == ticket.id)
        self.assertEqual(len(inbox), 1)
        self.assertIn("Réponse de Client Matrice", inbox.subject)

    def test_none_channel_silences_event(self):
        self._pref("client_reply", "none")
        ticket = self._ticket()
        before = len(self._notifs(self.agent))
        self._client_says(ticket)
        self.assertEqual(len(self._notifs(self.agent)), before)
        self.assertFalse(self.Item.search([]))

    def test_ntfy_without_relay_falls_back_to_inbox(self):
        self.env["ir.config_parameter"].sudo().set_param("bf_helpdesk.ntfy_webhook_url", "")
        self._pref("client_reply", "ntfy")
        ticket = self._ticket()
        self._client_says(ticket)
        self.assertTrue(self._notifs(self.agent, "inbox").filtered(
            lambda n: n.mail_message_id.res_id == ticket.id))

    def test_other_agents_keep_default(self):
        self._pref("client_reply", "none")
        ticket = self._ticket()
        ticket.message_subscribe(partner_ids=self.other.partner_id.ids)
        msg = self._client_says(ticket)
        self.assertTrue(self._notifs(self.other).filtered(lambda n: n.mail_message_id == msg))

    # ------------------------------------------------------------ condensés

    def test_hourly_condensed(self):
        self._pref("client_reply", "email", "hourly")
        t1, t2 = self._ticket(), self._ticket(name="Imprimante")
        self._client_says(t1)
        self._client_says(t2)
        items = self.Item.search([("user_id", "=", self.agent.id)])
        self.assertEqual(len(items), 2)
        self.Item._cron_send_agent_digests()
        self.assertTrue(all(items.mapped("sent")))
        condensed = self._notifs(self.agent, "email").mail_message_id.filtered(
            lambda m: m.model == "res.partner")
        self.assertEqual(len(condensed), 1)
        self.assertIn(t1.number, condensed.body)
        self.assertIn(t2.number, condensed.body)
        # Le courriel lui-même : gabarit maître, sans « -- System ».
        mail = self.env["mail.mail"].search([("mail_message_id", "=", condensed.id)])
        self.assertIn('class="bf_hd_mail"', mail.body_html)  # blocs de l'assistance
        self.assertIn("#F8FAFC", mail.body_html)  # dans la mise en page Blue Fox
        self.assertNotIn("Communication interne", mail.body_html)
        self.assertNotIn("-- ", mail.body_html)
        self.assertNotIn("Voir la demande", mail.body_html)

    def test_daily_condensed_once_after_eight(self):
        self._pref("client_reply", "odoo", "daily")
        ticket = self._ticket()
        self._client_says(ticket)
        with freeze_time("2026-10-06 11:00:00"):  # 7 h à Montréal
            self.Item._cron_send_agent_digests()
        self.assertFalse(self.Item.search([]).filtered("sent"))
        with freeze_time("2026-10-06 13:00:00"):  # 9 h
            self.Item._cron_send_agent_digests()
        self.assertTrue(all(self.Item.search([]).mapped("sent")))
        self._client_says(ticket, "Encore")
        with freeze_time("2026-10-06 15:00:00"):  # même jour : attend demain
            self.Item._cron_send_agent_digests()
        self.assertEqual(len(self.Item.search([("sent", "=", False)])), 1)

    def test_very_high_priority_is_always_immediate(self):
        self._pref("client_reply", "odoo", "daily")
        ticket = self._ticket(priority="3")
        self._client_says(ticket)
        self.assertFalse(self.Item.search([]))
        self.assertTrue(self._notifs(self.agent, "inbox").filtered(
            lambda n: n.mail_message_id.res_id == ticket.id))

    # ------------------------------------------------------------ autres événements

    def test_assignment_follows_pref(self):
        ticket = self._ticket()
        count = len(self._notifs(self.agent))
        ticket.with_context(tracking_disable=False).user_id = self.other
        self._pref("assigned", "none", user=self.other)
        ticket2 = self._ticket()
        before_other = len(self._notifs(self.other))
        ticket2.with_context(tracking_disable=False).user_id = self.other
        self.assertEqual(len(self._notifs(self.other)), before_other)
        self.assertEqual(len(self._notifs(self.agent)), count)

    def test_sla_risk_dispatched_on_transition(self):
        self.team.write({"sla_response_hours": 4.0, "sla_calendar_id": False})
        self._pref("sla_risk", "odoo")
        ticket = self._ticket(user_id=self.agent.id)
        # create_date suit l'horloge de la transaction, pas freezegun : on se
        # place par rapport à elle, 30 min avant l'échéance de 4 h.
        with freeze_time(ticket.create_date + timedelta(hours=3, minutes=30)):
            # Lire l'état ici le calcule déjà « à risque » (comme une écriture
            # du billet le ferait) : la tâche doit quand même signaler.
            self.assertEqual(ticket.sla_state, "at_risk")
            self.Ticket._cron_sla_breach_activity()
            self.Ticket._cron_sla_breach_activity()  # pas de doublon
        risk = self._notifs(self.agent, "inbox").mail_message_id.filtered(
            lambda m: "SLA à risque" in (m.subject or ""))
        self.assertEqual(len(risk), 1)

    # ------------------------------------------------------------ droits

    def test_agent_sees_only_own_prefs(self):
        self._pref("client_reply", "none", user=self.other)
        mine = self._pref("assigned", "email")
        visible = self.Pref.with_user(self.agent).search([])
        self.assertEqual(visible, mine)
        with self.assertRaises(AccessError):
            self.Pref.with_user(self.agent).search([], limit=1).sudo().search(
                [("user_id", "=", self.other.id)]).with_user(self.agent).read(["event"])

    # ------------------------------------------------------------ contexte d'un appelant

    def test_injected_dispatch_context_is_ignored(self):
        """Un appelant ne court-circuite pas le répartiteur par le contexte."""
        self._pref("client_reply", "none")
        ticket = self._ticket()
        before = len(self._notifs(self.agent))
        ticket.with_context(bf_hd_dispatch=True).message_post(
            body="Toujours lent.", author_id=self.client.id,
            message_type="email", subtype_xmlid="mail.mt_comment")
        self.assertEqual(len(self._notifs(self.agent)), before)

    def test_injected_forced_channel_is_ignored(self):
        """Ni liste, ni clés en chaîne, ni canal inconnu : le canal reste celui de l'agent."""
        ticket = self._ticket()
        pid = self.agent.partner_id.id
        sneaky = ["sms"] * (pid + 1)
        sneaky[0] = pid
        for forced in (sneaky, {str(pid): "sms"}, {pid: "sms"}):
            msg = ticket.with_context(bf_hd_force_notif=forced).message_post(
                body="Toujours lent.", author_id=self.client.id,
                message_type="email", subtype_xmlid="mail.mt_comment")
            notif = self._notifs(self.agent).filtered(lambda n: n.mail_message_id == msg)
            self.assertEqual(notif.notification_type, "email", type(forced).__name__)

