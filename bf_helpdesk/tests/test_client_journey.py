from datetime import date

from freezegun import freeze_time

from odoo.tests import HttpCase, TransactionCase, new_test_user, tagged


@tagged("bf_helpdesk", "bf_helpdesk_client_journey")
class TestClientJourney(TransactionCase):
    """Parcours client 18.0.4.7.0 : statut au portail, canal, accusé."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["res.lang"]._activate_lang("fr_CA")
        cls.env = cls.env(context=dict(cls.env.context, lang="fr_CA"))
        cls.env.company.partner_id.lang = "fr_CA"
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        cls.Ticket = cls.env["helpdesk.ticket"]
        cls.Mail = cls.env["mail.mail"]
        cls.web = cls.env.ref("helpdesk_mgmt.helpdesk_ticket_channel_web")
        cls.email = cls.env.ref("helpdesk_mgmt.helpdesk_ticket_channel_email")
        cls.phone = cls.env.ref("helpdesk_mgmt.helpdesk_ticket_channel_phone")
        cls.template = cls.env.ref("bf_helpdesk.mail_template_ticket_ack")
        model = cls.env.ref("helpdesk_mgmt.model_helpdesk_ticket")
        # Lundi au vendredi, 8 h 30 à 12 h et 13 h à 17 h, heure de Montréal.
        cls.calendar = cls.env["resource.calendar"].create({
            "name": "Parcours essai",
            "tz": "America/Toronto",
            "attendance_ids": [
                (0, 0, {"name": f"{d} {p}", "dayofweek": str(d),
                        "hour_from": h[0], "hour_to": h[1], "day_period": p})
                for d in range(5)
                for p, h in (("morning", (8.5, 12)), ("afternoon", (13, 17)))
            ],
        })
        cls.team = cls.env["helpdesk.ticket.team"].create({
            "name": "Parcours client",
            "alias_id": cls.env["mail.alias"].create({
                "alias_name": "parcours-client", "alias_model_id": model.id,
            }).id,
            "sla_response_hours": 4.0,
            "sla_calendar_id": cls.calendar.id,
            "ack_channel_ids": [(6, 0, (cls.web | cls.email).ids)],
        })
        cls.stages = cls.team._get_applicable_stages()
        cls.stage_new = cls.stages.filtered(lambda s: not s.closed)[:1]
        cls.stage_closed = cls.stages.filtered("closed")[:1]

    def _ticket(self, **vals):
        values = {
            "name": "Imprimante muette",
            "description": "<p>Plus rien ne sort.</p>",
            "team_id": self.team.id,
            "partner_name": "Jane Essai",
            "partner_email": "jane.essai@example.com",
            "channel_id": self.web.id,
        }
        values.update(vals)
        return self.Ticket.create(values)

    def _acks(self, ticket):
        return self.Mail.search([
            ("model", "=", "helpdesk.ticket"),
            ("res_id", "=", ticket.id),
            ("subject", "like", "Demande reçue"),
        ])

    # ------------------------------------------------------------ défauts

    def test_new_team_sends_nothing_by_default(self):
        # aucun envoi sans réglage, même pour une équipe neuve.
        model = self.env.ref("helpdesk_mgmt.model_helpdesk_ticket")
        team = self.env["helpdesk.ticket.team"].create({
            "name": "Neuve", "alias_id": self.env["mail.alias"].create({
                "alias_name": "neuve", "alias_model_id": model.id}).id})
        self.assertFalse(team.ack_channel_ids)
        self.assertEqual(team.csat_mode, "none")

    # ------------------------------------------------------------ statut

    def test_portal_status_uses_client_label(self):
        ticket = self._ticket()
        self.stage_new.portal_label = "Reçue"
        ticket.invalidate_recordset(["portal_status"])
        self.assertEqual(ticket.portal_status, "Reçue")
        self.stage_new.portal_label = False
        ticket.invalidate_recordset(["portal_status"])
        self.assertEqual(ticket.portal_status, self.stage_new.name)

    def test_portal_status_shows_waiting(self):
        ticket = self._ticket()
        ticket.waiting_state = "client"
        self.assertEqual(ticket.portal_status, "Votre réponse est attendue")
        self.assertIn("répondre", ticket.portal_status_hint)
        ticket.waiting_state = "external"
        self.assertEqual(ticket.portal_status, "En attente d'un tiers")

    def test_closed_ticket_hides_waiting(self):
        ticket = self._ticket(waiting_state="client")
        ticket.stage_id = self.stage_closed
        self.assertNotEqual(ticket.portal_status, "Votre réponse est attendue")
        self.assertFalse(ticket.portal_status_hint)

    @freeze_time("2026-09-28 13:00:00")  # lundi, 9 h à Montréal
    def test_portal_hint_announces_first_response(self):
        ticket = self._ticket()
        self.assertTrue(ticket.sla_response_deadline)
        self.assertIn("Première réponse prévue", ticket.portal_status_hint)
        # create_date vient de l'horloge de la transaction, pas de freezegun :
        # on compare à l'échéance réelle du billet, formatée pour le client.
        self.assertIn(
            ticket._bf_format_client_datetime(ticket.sla_response_deadline),
            ticket.portal_status_hint,
        )

    # ------------------------------------------------------------ accusé

    def test_ack_sent_once_for_retained_channel(self):
        ticket = self._ticket()
        acks = self._acks(ticket)
        self.assertEqual(len(acks), 1)
        self.assertTrue(ticket.ack_sent_date)
        self.assertIn("Auto-Submitted", acks.headers)
        self.assertIn("jane.essai@example.com", acks.email_to)
        # Relancer l'envoi ne double rien.
        ticket._bf_send_ack()
        self.assertEqual(len(self._acks(ticket)), 1)

    def test_ack_is_not_a_first_response(self):
        ticket = self._ticket()
        self.assertTrue(ticket.ack_sent_date)
        self.assertFalse(ticket.first_response_date)
        note = ticket.message_ids.filtered(
            lambda m: "Accusé de réception envoyé" in (m.body or "")
        )
        self.assertEqual(len(note), 1)
        self.assertTrue(note.subtype_id.internal)

    def test_no_ack_for_channel_not_retained(self):
        ticket = self._ticket(channel_id=self.phone.id)
        self.assertFalse(self._acks(ticket))
        self.assertFalse(ticket.ack_sent_date)

    def test_no_ack_without_channel_or_email(self):
        self.assertFalse(self._acks(self._ticket(channel_id=False)))
        self.assertFalse(self._acks(self._ticket(partner_email=False)))

    def test_no_ack_to_own_alias(self):
        ticket = self._ticket(partner_email="parcours-client@" + (
            self.team.alias_id.alias_domain or "example.com"))
        if self.team.alias_id.alias_domain:
            self.assertFalse(self._acks(ticket))

    def test_no_ack_when_stage_sends_its_own(self):
        tpl = self.env["mail.template"].create({
            "name": "Étape essai", "model_id": self.env.ref(
                "helpdesk_mgmt.model_helpdesk_ticket").id,
            "subject": "Étape",
        })
        self.stage_new.mail_template_id = tpl
        self.assertFalse(self._acks(self._ticket()))

    def test_ack_capped_per_sender_per_hour(self):
        tickets = [self._ticket(name=f"Boucle {i}") for i in range(7)]
        sent = [t for t in tickets if t.ack_sent_date]
        self.assertEqual(len(sent), self.Ticket.ACK_MAX_PER_SENDER_HOUR)

    def test_ack_skipped_on_import(self):
        ticket = self.Ticket.with_context(import_file=True).create({
            "name": "Importé", "team_id": self.team.id,
            "description": "<p>Importé</p>",
            "partner_email": "import@example.com", "channel_id": self.web.id,
        })
        self.assertFalse(ticket.ack_sent_date)

    @freeze_time("2026-09-28 13:00:00")
    def test_ack_body_carries_delay_hours_and_notice(self):
        self.team.write({
            "ack_notice_html": "<p>Bureaux fermés pour la fête.</p>",
            "ack_notice_until": date(2026, 9, 30),
        })
        body = self._acks(self._ticket()).body_html
        self.assertIn("au plus tard le", body)
        self.assertIn("du lundi au vendredi, de 8 h 30 à 17 h", body)
        self.assertIn("Bureaux fermés", body)

    @freeze_time("2026-10-05 13:00:00")
    def test_expired_notice_left_out(self):
        self.team.write({
            "ack_notice_html": "<p>Bureaux fermés pour la fête.</p>",
            "ack_notice_until": date(2026, 9, 30),
        })
        self.assertFalse(self.team.ack_notice_active)
        body = self._acks(self._ticket()).body_html
        self.assertNotIn("Bureaux fermés", body)

    # ------------------------------------------------------------ horaire

    def test_business_hours_summary(self):
        self.assertEqual(
            self.team.business_hours_summary,
            "du lundi au vendredi, de 8 h 30 à 17 h",
        )
        self.team.business_hours_text = "sur rendez-vous"
        self.assertEqual(self.team.business_hours_summary, "sur rendez-vous")

    def test_business_hours_summary_split_days(self):
        self.calendar.attendance_ids.filtered(
            lambda a: a.dayofweek == "4" and a.day_period == "afternoon"
        ).unlink()
        self.assertEqual(
            self.team.business_hours_summary,
            "du lundi au jeudi, de 8 h 30 à 17 h ; le vendredi, de 8 h 30 à 12 h",
        )

    # ------------------------------------------------------------ canal

    def test_inbound_email_records_channel(self):
        raw = (
            "From: Jane Essai <jane.courriel@example.com>\r\n"
            f"To: parcours-client@{self.team.alias_id.alias_domain or 'example.com'}\r\n"
            "Subject: Courriel entrant\r\n"
            "Message-ID: <essai-canal@example.com>\r\n"
            "Content-Type: text/plain; charset=utf-8\r\n\r\n"
            "Bonjour, rien ne marche.\r\n"
        )
        ticket = self.env["mail.thread"].with_context(
            mail_create_nolog=True,
        ).message_process("helpdesk.ticket", raw,
                          custom_values={"team_id": self.team.id})
        ticket = self.Ticket.browse(ticket)
        self.assertEqual(ticket.channel_id, self.email)
        self.assertTrue(ticket.ack_sent_date)


@tagged("post_install", "-at_install", "bf_helpdesk", "bf_helpdesk_client_journey")
class TestClientJourneyPortal(HttpCase):
    """Le portail ne montre jamais le nom interne d'une étape qui a un libellé."""

    def test_portal_pages_use_client_label(self):
        model = self.env.ref("helpdesk_mgmt.model_helpdesk_ticket")
        team = self.env["helpdesk.ticket.team"].create({
            "name": "Portail essai",
            "alias_id": self.env["mail.alias"].create({
                "alias_name": "portail-essai", "alias_model_id": model.id,
            }).id,
        })
        stage = team._get_applicable_stages().filtered(lambda s: not s.closed)[:1]
        stage.write({"name": "Triage interne essai", "portal_label": "Reçue"})
        user = new_test_user(self.env, login="client-portail-essai", groups="base.group_portal")
        ticket = self.env["helpdesk.ticket"].create({
            "name": "Billet du portail",
            "description": "<p>x</p>",
            "team_id": team.id,
            "stage_id": stage.id,
            "partner_id": user.partner_id.id,
        })
        ticket.message_subscribe(partner_ids=user.partner_id.ids)
        waiting = ticket.copy({"name": "Billet en attente", "waiting_state": "client"})
        waiting.message_subscribe(partner_ids=user.partner_id.ids)
        self.authenticate("client-portail-essai", "client-portail-essai")

        for url in ("/my/tickets", "/my/tickets?groupby=stage",
                    f"/my/ticket/{ticket.id}"):
            html = self.url_open(url).text
            self.assertNotIn("Triage interne essai", html, url)
            self.assertIn("Reçue", html, url)
        html = self.url_open(f"/my/ticket/{waiting.id}").text
        self.assertIn("Votre réponse est attendue", html)
        self.assertIn("Nous attendons votre réponse", html)
