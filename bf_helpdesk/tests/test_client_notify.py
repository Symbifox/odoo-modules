import re
from datetime import datetime, timedelta

from freezegun import freeze_time

from odoo.tests import HttpCase, TransactionCase, new_test_user, tagged


class NotifyCommon:
    @classmethod
    def _setup_notify(cls):
        cls.env["res.lang"]._activate_lang("fr_CA")
        cls.env.company.partner_id.lang = "fr_CA"
        cls.Ticket = cls.env["helpdesk.ticket"]
        cls.Mail = cls.env["mail.mail"]
        cls.Item = cls.env["helpdesk.client.digest.item"]
        model = cls.env.ref("helpdesk_mgmt.model_helpdesk_ticket")
        cls.team = cls.env["helpdesk.ticket.team"].create({
            "name": "Avis essai", "ack_channel_ids": [(5, 0, 0)], "csat_mode": "none",
            "alias_id": cls.env["mail.alias"].create({
                "alias_name": "avis-essai", "alias_model_id": model.id,
            }).id,
        })
        cls.stages = cls.team._get_applicable_stages()
        cls.client = cls.env["res.partner"].create({
            "name": "Robin Essai", "email": "robin@example.com", "lang": "fr_CA",
            "tz": "America/Toronto",
        })
        cls.agent = new_test_user(
            cls.env, login="agent-avis", email="agent.avis@example.com",
            groups="base.group_user,helpdesk_mgmt.group_helpdesk_user")
        cls.colleague = new_test_user(
            cls.env, login="collegue-avis", email="collegue@example.com",
            groups="base.group_user,helpdesk_mgmt.group_helpdesk_user")

    def _ticket(self, **vals):
        values = {"name": "Accès au NAS", "description": "<p>x</p>",
                  "team_id": self.team.id, "partner_id": self.client.id,
                  "stage_id": self.stages.filtered(lambda s: not s.closed)[:1].id}
        values.update(vals)
        ticket = self.Ticket.create(values)
        ticket.message_subscribe(partner_ids=(self.client | self.colleague.partner_id).ids)
        return ticket

    def _reply(self, ticket, body="Voici la marche à suivre."):
        return ticket.with_user(self.agent).message_post(
            body=body, message_type="comment", subtype_xmlid="mail.mt_comment")

    def _mails_to(self, partner, ticket=None):
        domain = [("recipient_ids", "in", partner.ids)]
        if ticket:
            domain += [("model", "=", "helpdesk.ticket"), ("res_id", "=", ticket.id)]
        return self.Mail.search(domain)


@tagged("bf_helpdesk", "bf_helpdesk_client_notify")
class TestClientNotify(NotifyCommon, TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True,
                                       mail_notify_force_send=False, lang="fr_CA"))
        cls._setup_notify()

    def test_default_each_reply_goes_out(self):
        ticket = self._ticket()
        self._reply(ticket)
        self.assertEqual(len(self._mails_to(self.client, ticket)), 1)
        self.assertFalse(self.Item.search([]))

    def test_daily_mode_queues_and_sends_one_summary(self):
        self.client.helpdesk_notify_mode = "daily"
        t1, t2 = self._ticket(), self._ticket(name="Imprimante")
        with freeze_time("2026-10-05 13:00:00"):  # 9 h à Montréal
            self._reply(t1, "Réponse A")
            self._reply(t2, "Réponse B")
        self.assertFalse(self._mails_to(self.client, t1))
        self.assertEqual(len(self.Item.search([("partner_id", "=", self.client.id)])), 2)
        # Le collègue interne, abonné, reçoit toujours ses avis.
        self.assertTrue(self._mails_to(self.colleague.partner_id, t1))
        with freeze_time("2026-10-06 11:30:00"):  # 7 h 30 : trop tôt
            self.Item._cron_send_client_digests()
        self.assertFalse(self._mails_to(self.client))
        with freeze_time("2026-10-06 12:30:00"):  # 8 h 30
            self.Item._cron_send_client_digests()
            self.Item._cron_send_client_digests()  # deuxième passage : rien
        digest = self._mails_to(self.client)
        self.assertEqual(len(digest), 1)
        self.assertIn("Réponse A", digest.body_html)
        self.assertIn("Réponse B", digest.body_html)
        self.assertIn(t1.number, digest.body_html)
        self.assertIn("(2)", digest.subject)

    def test_daily_mode_still_gets_info_requests_and_resolution(self):
        self.client.helpdesk_notify_mode = "daily"
        ticket = self._ticket()
        ticket.waiting_state = "client"
        self._reply(ticket, "Pouvez-vous m'envoyer une capture ?")
        self.assertEqual(len(self._mails_to(self.client, ticket)), 1)
        ticket.waiting_state = False
        ticket.stage_id = self.stages.filtered("closed")[:1]
        self._reply(ticket, "C'est réglé.")
        self.assertEqual(len(self._mails_to(self.client, ticket)), 2)
        self.assertFalse(self.Item.search([("ticket_id", "=", ticket.id)]))

    def test_muted_ticket_gets_nothing_but_mandatory(self):
        ticket = self._ticket()
        ticket._bf_set_muted(self.client, True)
        self._reply(ticket)
        self.assertFalse(self._mails_to(self.client, ticket))
        self.assertFalse(self.Item.search([("ticket_id", "=", ticket.id)]))
        ticket.waiting_state = "client"
        self._reply(ticket, "Il nous manque votre numéro de série.")
        self.assertEqual(len(self._mails_to(self.client, ticket)), 1)
        ticket._bf_set_muted(self.client, False)
        ticket.waiting_state = False
        self._reply(ticket, "Merci, on s'en occupe.")
        self.assertEqual(len(self._mails_to(self.client, ticket)), 2)

    def test_internal_note_and_client_messages_untouched(self):
        self.client.helpdesk_notify_mode = "daily"
        ticket = self._ticket()
        ticket.with_user(self.agent).message_post(
            body="Note", message_type="comment", subtype_xmlid="mail.mt_note")
        self.assertFalse(self.Item.search([("ticket_id", "=", ticket.id)]))

    def test_mute_link_is_personal(self):
        ticket = self._ticket()
        self._reply(ticket)
        mail = self._mails_to(self.client, ticket)
        # L'ordre de l'envoi réel : liens rendus absolus, puis personnalisés.
        body = mail._personalize_outgoing_body(mail._prepare_outgoing_body(), self.client)
        # L'attribut entier : une adresse collée devant (https://…comhttps://…)
        # contient aussi le lien, et passait.
        self.assertIn('href="%s"' % ticket._bf_mute_url(self.client.id), body)
        self.assertNotIn("__bf_mute__", body)
        other = mail._personalize_outgoing_body(mail._prepare_outgoing_body(), self.colleague.partner_id)
        self.assertNotIn("/helpdesk/courriels/", other)
        self.assertNotEqual(ticket._bf_mute_token(self.client.id),
                            ticket._bf_mute_token(self.colleague.partner_id.id))


@tagged("post_install", "-at_install", "bf_helpdesk", "bf_helpdesk_client_notify")
class TestClientNotifyPages(NotifyCommon, HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_notify()

    def _csrf(self, html):
        return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)

    def test_link_get_does_not_mute_post_does(self):
        ticket = self._ticket()
        url = ticket._bf_mute_url(self.client.id).split("8069", 1)[-1]
        url = url[url.index("/helpdesk/"):]
        html = self.url_open(url).text
        ticket.invalidate_recordset()
        self.assertFalse(ticket.client_muted_partner_ids)
        self.url_open(url, data={"csrf_token": self._csrf(html), "action": "mute"})
        ticket.invalidate_recordset()
        self.assertEqual(ticket.client_muted_partner_ids, self.client)

    def test_bad_token_is_refused(self):
        ticket = self._ticket()
        html = self.url_open(f"/helpdesk/courriels/{ticket.id}/{self.client.id}/faux").text
        self.assertIn("plus actif", html)

    def test_portal_toggle_and_preference(self):
        user = new_test_user(self.env, login="robin-portail", groups="base.group_portal",
                             partner_id=self.client.id)
        ticket = self._ticket()
        self.authenticate("robin-portail", "robin-portail")
        html = self.url_open(f"/my/ticket/{ticket.id}").text
        # Le portail parle la langue de l'usager (en_US dans l'essai).
        self.assertTrue("Stop emails for this request" in html
                        or "Ne plus recevoir les courriels de cette demande" in html)
        self.url_open(f"/my/ticket/{ticket.id}/courriels",
                      data={"csrf_token": self._csrf(html), "action": "mute"})
        ticket.invalidate_recordset()
        self.assertIn(self.client, ticket.client_muted_partner_ids)
        html = self.url_open("/my/tickets").text
        self.assertTrue("in a daily summary" in html or "dans un résumé quotidien" in html)
        self.url_open("/my/helpdesk/preferences",
                      data={"csrf_token": self._csrf(html), "mode": "daily"})
        self.client.invalidate_recordset()
        self.assertEqual(self.client.helpdesk_notify_mode, "daily")
        self.assertTrue(user)
