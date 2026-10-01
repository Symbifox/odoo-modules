from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.bf_helpdesk.models.res_company import _rgb, brand_palette, contrast

WHITE = (255, 255, 255)


@tagged("bf_helpdesk", "bf_helpdesk_mail_layout")
class TestBrandPalette(TransactionCase):

    def _check(self, color):
        brand, text, link = brand_palette(color)
        self.assertGreaterEqual(contrast(_rgb(brand), _rgb(text)), 4.5, color)
        self.assertGreaterEqual(contrast(_rgb(link), WHITE), 4.5, color)
        return brand, text, link

    def test_bf_blue_is_made_readable(self):
        brand, text, link = self._check("#29ABE2")
        # Le bleu Blue Fox reste l'aplat ; le texte posé dessus est foncé.
        self.assertEqual(brand, "#29ABE2")
        self.assertEqual(text, "#1F2328")
        self.assertNotEqual(link, "#29ABE2")

    def test_various_brands(self):
        for color in ("#1f84af", "#e17a4b", "#b7a97f", "#FFFF00", "#000000",
                      "#714B67", "#777777"):
            self._check(color)

    def test_invalid_color_falls_back(self):
        self._check("pas une couleur")
        self._check(False)

    def test_company_fields(self):
        company = self.env.company
        # La couleur de la mise en page Blue Fox fait foi.
        company.report_brand_primary = "#29ABE2"
        company.email_primary_color = "#FF0000"
        self.assertEqual(company.helpdesk_brand_color, "#29ABE2")
        self.assertEqual(company.helpdesk_brand_text_color, "#1F2328")


@tagged("bf_helpdesk", "bf_helpdesk_mail_layout")
class TestMailLayout(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["res.lang"]._activate_lang("fr_CA")
        cls.env = cls.env(context=dict(cls.env.context, lang="fr_CA"))
        cls.env.company.partner_id.lang = "fr_CA"
        # Les avis partent en file (sinon, envoyés sur-le-champ puis effacés,
        # il n'en reste rien à lire).
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True,
                                       mail_notify_force_send=False))
        cls.Ticket = cls.env["helpdesk.ticket"]
        cls.Mail = cls.env["mail.mail"]
        model = cls.env.ref("helpdesk_mgmt.model_helpdesk_ticket")
        cls.team = cls.env["helpdesk.ticket.team"].create({
            "name": "Gabarit essai",
            "alias_id": cls.env["mail.alias"].create({
                "alias_name": "gabarit-essai", "alias_model_id": model.id,
            }).id,
            "ack_channel_ids": [(6, 0, cls.env.ref(
                "helpdesk_mgmt.helpdesk_ticket_channel_web").ids)],
            "csat_mode": "native",
            "csat_delay_hours": 0,
        })
        cls.env.company.report_brand_primary = "#29ABE2"
        cls.client = cls.env["res.partner"].create({
            "name": "Sam Essai", "email": "sam.essai@example.com", "lang": "fr_CA",
        })
        cls.agent = new_test_user(
            cls.env, login="agent-gabarit", email="agent.gabarit@example.com",
            groups="base.group_user,helpdesk_mgmt.group_helpdesk_user",
        )

    def _ticket(self, **vals):
        values = {
            "name": "Écran noir au démarrage",
            "description": "<p>Plus rien ne s'affiche.</p>",
            "team_id": self.team.id,
            "partner_id": self.client.id,
            "partner_email": self.client.email,
            "channel_id": self.env.ref("helpdesk_mgmt.helpdesk_ticket_channel_web").id,
        }
        values.update(vals)
        ticket = self.Ticket.create(values)
        ticket.message_subscribe(partner_ids=self.client.ids)
        return ticket

    def _mails_to_client(self, ticket):
        return self.Mail.search([
            ("recipient_ids", "in", self.client.ids),
            ("model", "=", "helpdesk.ticket"), ("res_id", "=", ticket.id),
        ], order="id")

    # ------------------------------------------------------------ sujet

    def test_subject_is_stable_and_tagged(self):
        ticket = self._ticket()
        self.assertEqual(ticket._message_compute_subject(),
                         f"[{ticket.number}] Écran noir au démarrage")
        ticket.name = "Carte graphique HS (triage interne)"
        self.assertEqual(ticket._message_compute_subject(),
                         f"[{ticket.number}] Écran noir au démarrage")

    # ------------------------------------------------------------ réponses du fil

    def test_agent_reply_uses_master_layout_with_history(self):
        ticket = self._ticket()
        ticket.message_post(
            body="Premier message du client.", author_id=self.client.id,
            message_type="email", subtype_xmlid="mail.mt_comment",
        )
        ticket.with_user(self.agent).message_post(
            body="Essayez de débrancher l'écran.",
            message_type="comment", subtype_xmlid="mail.mt_comment",
            partner_ids=self.client.ids,
        )
        mail = self._mails_to_client(ticket)[-1:]
        self.assertTrue(mail, "la réponse de l'agent doit partir au client")
        body = mail.body_html
        self.assertIn('role="presentation"', body)
        self.assertIn('lang="', body)
        # Mise en page Blue Fox (copie de secours ici : bluefox_branding absent)
        # autour des blocs de l'assistance.
        self.assertIn("#F8FAFC", body)
        self.assertIn('class="bf_hd_mail"', body)
        self.assertEqual(body.count("Voir la demande"), 1)  # l'aperçu caché n'en fait pas un second
        self.assertNotIn("potentialAction", body)  # le bouton de la mise en page commune est coupé
        self.assertIn(f"Demande {ticket.number}", body)
        self.assertIn("#29ABE2", body)
        self.assertIn("Messages précédents", body)
        self.assertIn("Premier message du client.", body)
        self.assertIn(f"[{ticket.number}]", mail.subject)

    def test_history_date_in_client_time_not_company(self):
        """La société est dans un autre fuseau : l'historique garde l'heure du client."""
        self.env.company.partner_id.tz = "Asia/Tokyo"
        ticket = self._ticket()
        ticket.team_id.sla_calendar_id = self.env["resource.calendar"].create({
            "name": "Montréal", "tz": "America/Toronto"})
        old = ticket.message_post(
            body="Premier message du client.", author_id=self.client.id,
            message_type="email", subtype_xmlid="mail.mt_comment")
        old.date = "2026-09-30 02:12:23"
        ticket.with_user(self.agent).message_post(
            body="Réponse.", message_type="comment", subtype_xmlid="mail.mt_comment",
            partner_ids=self.client.ids)
        body = self._mails_to_client(ticket)[-1:].body_html
        self.assertIn(ticket._bf_format_client_datetime(old.date), body)
        self.assertIn("22h12", body)
        self.assertNotIn("15:12", body)

    def test_internal_note_quotes_nothing(self):
        ticket = self._ticket()
        ticket.message_post(body="Question du client.", author_id=self.client.id,
                            message_type="email", subtype_xmlid="mail.mt_comment")
        note = ticket.with_user(self.agent).message_post(
            body="Note interne.", message_type="comment", subtype_xmlid="mail.mt_note")
        self.assertFalse(ticket._bf_mail_history(note))

    def test_bluefox_layout_request_is_replaced(self):
        ticket = self._ticket()
        ticket.with_user(self.agent).message_post(
            body="Mise à jour.", message_type="comment",
            subtype_xmlid="mail.mt_comment", partner_ids=self.client.ids,
            email_layout_xmlid="mail.mail_notification_light",
        )
        body = self._mails_to_client(ticket)[-1:].body_html
        self.assertIn(f"Demande {ticket.number}", body)
        self.assertNotIn("Powered by", body)

    # ------------------------------------------------------------ gabarits automatiques

    def test_ack_and_csat_go_through_master_layout(self):
        ticket = self._ticket()
        ack = self.Mail.search([("res_id", "=", ticket.id),
                                ("subject", "like", "Demande reçue")])
        self.assertIn(f"Demande {ticket.number}", ack.body_html)
        self.assertIn("Référence", ack.body_html)
        ticket.stage_id = self.team._get_applicable_stages().filtered("closed")[:1]
        csat = self.Mail.search([("subject", "like", "Votre avis sur la demande [%s]" % ticket.number)])
        self.assertIn(f"Demande {ticket.number}", csat.body_html)

    # ------------------------------------------------------------ routage par le sujet

    def _incoming(self, sender, subject, msgid):
        raw = (
            f"From: {sender}\r\n"
            "To: gabarit-essai@example.com\r\n"
            f"Subject: {subject}\r\n"
            f"Message-ID: <{msgid}@example.com>\r\n"
            "Content-Type: text/plain; charset=utf-8\r\n\r\n"
            "Encore moi.\r\n"
        )
        return self.env["mail.thread"].message_process(
            "helpdesk.ticket", raw, custom_values={"team_id": self.team.id})

    def test_subject_tag_routes_client_reply(self):
        ticket = self._ticket()
        before = self.Ticket.search_count([])
        res_id = self._incoming("Sam Essai <sam.essai@example.com>",
                                f"RE: [{ticket.number}] Écran noir", "sujet-1")
        self.assertEqual(res_id, ticket.id)
        self.assertEqual(self.Ticket.search_count([]), before)
        last = ticket.message_ids.sorted("id")[-1]
        self.assertEqual(last.subtype_id, self.env.ref("mail.mt_comment"))

    def test_subject_tag_from_stranger_opens_new_ticket(self):
        ticket = self._ticket()
        res_id = self._incoming("Intrus <intrus@example.com>",
                                f"[{ticket.number}] Je devine un numéro", "sujet-2")
        self.assertNotEqual(res_id, ticket.id)
        self.assertNotIn("Encore moi", "".join(ticket.message_ids.mapped("body")))


@tagged("bf_helpdesk", "bf_helpdesk_mail_layout")
class TestEnglishEmails(TransactionCase):
    """Chaque gabarit a sa version anglaise, choisie par la langue du client."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["res.lang"]._activate_lang("fr_CA")
        cls.env["res.lang"]._activate_lang("en_CA")
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True,
                                       mail_notify_force_send=False, lang="fr_CA"))
        cls.env.company.partner_id.lang = "fr_CA"
        cls.Ticket = cls.env["helpdesk.ticket"]
        cls.Mail = cls.env["mail.mail"]
        model = cls.env.ref("helpdesk_mgmt.model_helpdesk_ticket")
        calendar = cls.env["resource.calendar"].create({
            "name": "EN 40 h", "tz": "America/Toronto",
            "attendance_ids": [
                (0, 0, {"name": f"{d} {p}", "dayofweek": str(d),
                        "hour_from": h[0], "hour_to": h[1], "day_period": p})
                for d in range(5)
                for p, h in (("morning", (8.5, 12)), ("afternoon", (13, 17)))
            ],
        })
        cls.team = cls.env["helpdesk.ticket.team"].create({
            "name": "Support EN",
            "alias_id": cls.env["mail.alias"].create({
                "alias_name": "support-en", "alias_model_id": model.id,
            }).id,
            "ack_channel_ids": [(6, 0, cls.env.ref(
                "helpdesk_mgmt.helpdesk_ticket_channel_web").ids)],
            "csat_mode": "native", "csat_delay_hours": 0,
            "sla_response_hours": 4.0, "sla_calendar_id": calendar.id,
            "reminder_enabled": True,
        })
        cls.en_client = cls.env["res.partner"].create({
            "name": "Jordan Test", "email": "jordan@example.com", "lang": "en_CA",
        })
        cls.fr_client = cls.env["res.partner"].create({
            "name": "Maxime Essai", "email": "maxime@example.com", "lang": "fr_CA",
        })
        cls.agent = new_test_user(
            cls.env, login="agent-en", email="agent.en@example.com",
            groups="base.group_user,helpdesk_mgmt.group_helpdesk_user",
        )

    def _ticket(self, partner, **vals):
        values = {
            "name": "Printer not printing", "description": "<p>x</p>",
            "team_id": self.team.id, "partner_id": partner.id,
            "partner_email": partner.email,
            "channel_id": self.env.ref("helpdesk_mgmt.helpdesk_ticket_channel_web").id,
        }
        values.update(vals)
        ticket = self.Ticket.create(values)
        ticket.message_subscribe(partner_ids=partner.ids)
        return ticket

    def _mail(self, ticket, word):
        return self.Mail.search([("res_id", "=", ticket.id),
                                 ("subject", "like", word)], limit=1)

    def test_client_lang_follows_partner(self):
        self.assertEqual(self._ticket(self.en_client).client_lang, "en_CA")
        self.assertEqual(self._ticket(self.fr_client).client_lang, "fr_CA")
        anonymous = self.Ticket.create({
            "name": "Anon", "description": "<p>x</p>", "team_id": self.team.id,
            "partner_email": "anon@example.com",
        })
        self.assertEqual(anonymous.client_lang, "fr_CA")

    def test_ack_in_english(self):
        ticket = self._ticket(self.en_client)
        ack = self._mail(ticket, "Request received")
        self.assertTrue(ack)
        self.assertIn("We have received your request", ack.body_html)
        self.assertIn("You will receive a first reply by", ack.body_html)
        self.assertIn("Monday to Friday, 8:30 a.m. to 5 p.m.", ack.body_html)
        self.assertIn(f"Request {ticket.number}", ack.body_html)
        self.assertIn("To add details, reply to this email", ack.body_html)
        self.assertNotIn("Nous avons bien reçu", ack.body_html)

    def test_ack_stays_french_for_french_client(self):
        ticket = self._ticket(self.fr_client)
        ack = self._mail(ticket, "Demande reçue")
        self.assertIn("Nous avons bien reçu votre demande", ack.body_html)
        self.assertIn("du lundi au vendredi, de 8 h 30 à 17 h", ack.body_html)
        self.assertIn(f"Demande {ticket.number}", ack.body_html)

    def test_reminder_and_close_in_english(self):
        ticket = self._ticket(self.en_client)
        ticket._bf_send_client_mail(
            self.env.ref("bf_helpdesk.mail_template_waiting_reminder"),
            "auto-generated", "Relance envoyée à %s.")
        self.assertIn("We are waiting for your reply",
                      self._mail(ticket, "Reminder").body_html)
        ticket._bf_send_client_mail(
            self.env.ref("bf_helpdesk.mail_template_waiting_autoclose"),
            "auto-generated", "Fermeture envoyée à %s.")
        self.assertIn("we have closed your request",
                      self._mail(ticket, "Request closed").body_html)

    def test_csat_in_english(self):
        ticket = self._ticket(self.en_client)
        ticket.stage_id = self.team._get_applicable_stages().filtered("closed")[:1]
        mail = self._mail(ticket, "Your feedback on request")
        self.assertIn("Very satisfied", mail.body_html)
        self.assertIn("How satisfied are you", mail.body_html)

    def test_agent_reply_layout_in_english(self):
        ticket = self._ticket(self.en_client)
        ticket.message_post(body="First message.", author_id=self.en_client.id,
                            message_type="email", subtype_xmlid="mail.mt_comment")
        ticket.with_user(self.agent).message_post(
            body="Please restart it.", message_type="comment",
            subtype_xmlid="mail.mt_comment", partner_ids=self.en_client.ids)
        mail = self.Mail.search([("recipient_ids", "in", self.en_client.ids),
                                 ("res_id", "=", ticket.id)], order="id desc", limit=1)
        self.assertIn("Previous messages", mail.body_html)
        self.assertIn(f"Request {ticket.number}", mail.body_html)
        self.assertIn('lang="en-CA"', mail.body_html)
