from datetime import datetime, timedelta

from freezegun import freeze_time

from odoo.tests import TransactionCase, new_test_user, tagged

T0 = datetime(2026, 10, 5, 14, 0)  # lundi


@tagged("bf_helpdesk", "bf_helpdesk_reminders")
class TestWaitingReminders(TransactionCase):
    """Relances d'un billet en attente du client (18.0.4.7.0)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["res.lang"]._activate_lang("fr_CA")
        cls.env = cls.env(context=dict(cls.env.context, lang="fr_CA"))
        cls.env.company.partner_id.lang = "fr_CA"
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        cls.Ticket = cls.env["helpdesk.ticket"]
        cls.Mail = cls.env["mail.mail"]
        model = cls.env.ref("helpdesk_mgmt.model_helpdesk_ticket")
        cls.team = cls.env["helpdesk.ticket.team"].create({
            "name": "Relances essai",
            "alias_id": cls.env["mail.alias"].create({
                "alias_name": "relances-essai", "alias_model_id": model.id,
            }).id,
            # Heures civiles : 3 jours = 72 h, plus simple à lire.
            "sla_calendar_id": False,
            "ack_channel_ids": [(5, 0, 0)],
            "reminder_enabled": True,
        })
        stages = cls.team._get_applicable_stages()
        cls.stage_open = stages.filtered(lambda s: not s.closed)[:1]
        cls.stage_closed = stages.filtered("closed")[:1]
        cls.team.reminder_close_stage_id = cls.stage_closed
        cls.client = cls.env["res.partner"].create({
            "name": "Dominique Essai", "email": "dominique.essai@example.com", "lang": "fr_CA",
        })
        cls.agent = new_test_user(
            cls.env, login="agent-relances",
            groups="base.group_user,helpdesk_mgmt.group_helpdesk_user",
        )

    def _waiting_ticket(self, **vals):
        values = {
            "name": "Accès VPN",
            "description": "<p>Le VPN refuse ma connexion.</p>",
            "team_id": self.team.id,
            "stage_id": self.stage_open.id,
            "partner_id": self.client.id,
            "partner_email": self.client.email,
        }
        values.update(vals)
        with freeze_time(T0):
            ticket = self.Ticket.create(values)
            ticket.waiting_state = "client"
        return ticket

    def _run(self, when):
        with freeze_time(when):
            self.Ticket._cron_waiting_reminders()

    def _mails(self, ticket, word):
        return self.Mail.search([
            ("model", "=", "helpdesk.ticket"), ("res_id", "=", ticket.id),
            ("subject", "like", word),
        ])

    def _client_says(self, ticket, body="Voici l'information demandée."):
        ticket.message_post(
            body=body, author_id=self.client.id,
            message_type="email", subtype_xmlid="mail.mt_comment",
        )

    # ------------------------------------------------------------ cycle

    def test_full_cycle_two_reminders_then_close(self):
        ticket = self._waiting_ticket()
        self._run(T0 + timedelta(days=2, hours=23))
        self.assertFalse(self._mails(ticket, "Rappel"))

        self._run(T0 + timedelta(days=3, hours=1))
        first = self._mails(ticket, "Rappel")
        self.assertEqual(len(first), 1)
        self.assertEqual(ticket.reminder_count, 1)
        self.assertNotIn("fermerons", first.body_html)
        self.assertIn("auto-generated", first.headers)

        # Deuxième relance : 4 jours après la première.
        self._run(T0 + timedelta(days=6, hours=23))
        self.assertEqual(ticket.reminder_count, 1)
        self._run(T0 + timedelta(days=7, hours=2))
        self.assertEqual(ticket.reminder_count, 2)
        second = self._mails(ticket, "Rappel") - first
        self.assertIn("fermerons", second.body_html)

        # Fermeture : 3 jours après la deuxième.
        self._run(T0 + timedelta(days=10, hours=3))
        self.assertEqual(ticket.stage_id, self.stage_closed)
        self.assertTrue(ticket.reminder_auto_closed)
        self.assertFalse(ticket.waiting_state)
        self.assertEqual(len(self._mails(ticket, "Demande fermée")), 1)

        # Plus rien ensuite.
        self._run(T0 + timedelta(days=30))
        self.assertEqual(len(self._mails(ticket, "Rappel")), 2)

    def test_reminders_are_not_first_responses(self):
        ticket = self._waiting_ticket()
        self._run(T0 + timedelta(days=4))
        self.assertEqual(ticket.reminder_count, 1)
        self.assertFalse(ticket.first_response_date)

    def test_autoclose_sends_no_csat(self):
        survey = self.env["survey.survey"].create({"title": "CSAT essai"})
        self.team.csat_survey_id = survey
        ticket = self._waiting_ticket()
        for day in (4, 8, 12):
            self._run(T0 + timedelta(days=day))
        self.assertTrue(ticket.stage_id.closed)
        self.assertFalse(ticket.csat_user_input_id)

    # ------------------------------------------------------------ réponses

    def test_client_reply_ends_waiting_and_resets(self):
        ticket = self._waiting_ticket()
        self._run(T0 + timedelta(days=4))
        self.assertEqual(ticket.reminder_count, 1)
        self._client_says(ticket)
        self.assertFalse(ticket.waiting_state)
        self.assertEqual(ticket.reminder_count, 0)
        self.assertFalse(ticket.reminder_next_date)

    def test_client_reply_reopens_auto_closed_ticket(self):
        ticket = self._waiting_ticket()
        for day in (4, 8, 12):
            self._run(T0 + timedelta(days=day))
        self.assertTrue(ticket.reminder_auto_closed)
        self._client_says(ticket, "Désolé du délai, c'est encore brisé.")
        self.assertFalse(ticket.stage_id.closed)
        self.assertFalse(ticket.reminder_auto_closed)
        self.assertTrue(ticket.message_ids.filtered(
            lambda m: "Rouvert" in (m.body or "")))

    def test_client_reply_leaves_manually_closed_ticket_closed(self):
        ticket = self._waiting_ticket()
        ticket.stage_id = self.stage_closed
        self._client_says(ticket, "Merci !")
        self.assertTrue(ticket.stage_id.closed)

    def test_agent_public_reply_restarts_cycle(self):
        ticket = self._waiting_ticket()
        self._run(T0 + timedelta(days=4))
        self.assertEqual(ticket.reminder_count, 1)
        with freeze_time(T0 + timedelta(days=5)):
            ticket.with_user(self.agent).message_post(
                body="Pouvez-vous essayer ceci ?",
                message_type="comment", subtype_xmlid="mail.mt_comment",
            )
        self.assertEqual(ticket.reminder_count, 0)
        self.assertTrue(ticket.waiting_state)
        # Le délai repart de la réponse de l'agent (jour 5) : 3 jours.
        self._run(T0 + timedelta(days=7, hours=23))
        self.assertEqual(ticket.reminder_count, 0)
        self._run(T0 + timedelta(days=8, hours=1))
        self.assertEqual(ticket.reminder_count, 1)

    def test_internal_note_does_not_restart_cycle(self):
        ticket = self._waiting_ticket()
        self._run(T0 + timedelta(days=4))
        ticket.with_user(self.agent).message_post(
            body="Note interne", message_type="comment",
            subtype_xmlid="mail.mt_note",
        )
        self.assertEqual(ticket.reminder_count, 1)
        self.assertTrue(ticket.waiting_state)

    # ------------------------------------------------------------ exclusions

    def test_excluded_ticket_gets_nothing(self):
        ticket = self._waiting_ticket(reminder_excluded=True)
        self._run(T0 + timedelta(days=20))
        self.assertFalse(self._mails(ticket, "Rappel"))
        self.assertFalse(ticket.reminder_next_date)

    def test_team_disabled_gets_nothing(self):
        self.team.reminder_enabled = False
        ticket = self._waiting_ticket()
        self._run(T0 + timedelta(days=20))
        self.assertFalse(self._mails(ticket, "Rappel"))

    def test_without_close_stage_agent_takes_over(self):
        self.team.reminder_close_stage_id = False
        ticket = self._waiting_ticket()
        for day in (4, 8, 12, 20):
            self._run(T0 + timedelta(days=day))
        self.assertFalse(ticket.stage_id.closed)
        self.assertEqual(ticket.reminder_count, 3)
        self.assertEqual(len(ticket.activity_ids.filtered(
            lambda a: a.summary == "Sans réponse du client")), 1)
        self.assertEqual(len(self._mails(ticket, "Rappel")), 2)

    def test_without_address_agent_takes_over(self):
        partner = self.env["res.partner"].create({"name": "Sans courriel", "lang": "fr_CA"})
        ticket = self._waiting_ticket(partner_id=partner.id, partner_email=False)
        self._run(T0 + timedelta(days=4))
        self.assertFalse(self._mails(ticket, "Rappel"))
        self.assertEqual(ticket.reminder_count, 3)
        self.assertTrue(ticket.activity_ids)

    def test_business_days_with_calendar(self):
        calendar = self.env["resource.calendar"].create({
            "name": "Relances 40 h", "tz": "UTC",
            "attendance_ids": [
                (0, 0, {"name": f"{d} {p}", "dayofweek": str(d),
                        "hour_from": h[0], "hour_to": h[1], "day_period": p})
                for d in range(5)
                for p, h in (("morning", (8, 12)), ("afternoon", (13, 17)))
            ],
        })
        self.team.sla_calendar_id = calendar
        # Vendredi 14 h : 3 jours ouvrés = mercredi 14 h, pas lundi.
        friday = datetime(2026, 10, 9, 14, 0)
        with freeze_time(friday):
            ticket = self.Ticket.create({
                "name": "Vendredi", "description": "<p>x</p>",
                "team_id": self.team.id, "stage_id": self.stage_open.id,
                "partner_id": self.client.id, "waiting_state": "client",
            })
        self.assertEqual(ticket._bf_reminder_due_date(), datetime(2026, 10, 14, 14, 0))

    # ------------------------------------------------------------ courriel

    def _reply_to(self, mail, sender, extra_headers=""):
        raw = (
            f"From: {sender}\r\n"
            "To: relances-essai@example.com\r\n"
            f"Subject: Re: {mail.subject}\r\n"
            f"Message-ID: <reponse-{mail.id}-{len(extra_headers)}@example.com>\r\n"
            f"In-Reply-To: {mail.message_id}\r\n"
            f"References: {mail.message_id}\r\n"
            f"{extra_headers}"
            "Content-Type: text/plain; charset=utf-8\r\n\r\n"
            "Réponse.\r\n"
        )
        return self.env["mail.thread"].message_process("helpdesk.ticket", raw)

    def test_email_reply_to_reminder_lands_on_ticket(self):
        ticket = self._waiting_ticket()
        self._run(T0 + timedelta(days=4))
        reminder = self._mails(ticket, "Rappel")
        count = self.Ticket.search_count([])
        res_id = self._reply_to(reminder, "Dominique Essai <dominique.essai@example.com>")
        self.assertEqual(res_id, ticket.id)
        self.assertEqual(self.Ticket.search_count([]), count)
        self.assertFalse(ticket.waiting_state)

    def test_out_of_office_is_not_a_reply(self):
        ticket = self._waiting_ticket()
        self._run(T0 + timedelta(days=4))
        reminder = self._mails(ticket, "Rappel")
        self._reply_to(reminder, "Dominique Essai <dominique.essai@example.com>",
                       "Auto-Submitted: auto-replied\r\n")
        self.assertEqual(ticket.waiting_state, "client")
        self.assertEqual(ticket.reminder_count, 1)
