import re
from datetime import datetime, timedelta

from freezegun import freeze_time

from odoo.tests import HttpCase, TransactionCase, new_test_user, tagged

T0 = datetime(2026, 10, 5, 14, 0)


class CsatCommon:
    @classmethod
    def _setup_csat(cls):
        cls.env["res.lang"]._activate_lang("fr_CA")
        cls.Ticket = cls.env["helpdesk.ticket"]
        cls.Csat = cls.env["helpdesk.ticket.csat"]
        model = cls.env.ref("helpdesk_mgmt.model_helpdesk_ticket")
        cls.team = cls.env["helpdesk.ticket.team"].create({
            "name": "Satisfaction essai",
            "alias_id": cls.env["mail.alias"].create({
                "alias_name": "satisfaction-essai", "alias_model_id": model.id,
            }).id,
            "ack_channel_ids": [(5, 0, 0)],
            "csat_mode": "native",
            "csat_delay_hours": 24,
        })
        stages = cls.team._get_applicable_stages()
        cls.stage_open = stages.filtered(lambda s: not s.closed)[:1]
        cls.stage_closed = stages.filtered("closed")[:1]
        cls.client = cls.env["res.partner"].create({
            "name": "Alex Essai", "email": "alex.essai@example.com", "lang": "fr_CA",
        })
        cls.agent = new_test_user(
            cls.env, login="agent-csat",
            groups="base.group_user,helpdesk_mgmt.group_helpdesk_user",
        )

    def _closed_ticket(self, when=T0):
        with freeze_time(when):
            ticket = self.Ticket.create({
                "name": "Courriel qui ne part pas",
                "description": "<p>x</p>",
                "team_id": self.team.id,
                "stage_id": self.stage_open.id,
                "partner_id": self.client.id,
                "partner_email": self.client.email,
                "user_id": self.agent.id,
            })
            ticket.stage_id = self.stage_closed
        return ticket

    def _mail(self, ticket):
        return self.env["mail.mail"].search([
            ("subject", "like", "Votre avis sur la demande [%s]" % ticket.number),
        ])


@tagged("bf_helpdesk", "bf_helpdesk_csat")
class TestCsatV2(CsatCommon, TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["res.lang"]._activate_lang("fr_CA")
        cls.env = cls.env(context=dict(cls.env.context, lang="fr_CA"))
        cls.env.company.partner_id.lang = "fr_CA"
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        cls._setup_csat()

    def _run(self, when):
        with freeze_time(when):
            self.Csat._cron_csat()

    def test_scheduled_then_sent_after_delay(self):
        ticket = self._closed_ticket()
        csat = ticket.csat_ids
        self.assertEqual(csat.state, "scheduled")
        self.assertEqual(csat.user_id, self.agent)
        self._run(T0 + timedelta(hours=23))
        self.assertEqual(csat.state, "scheduled")
        self._run(T0 + timedelta(hours=25))
        self.assertEqual(csat.state, "sent")
        mail = self._mail(ticket)
        self.assertEqual(len(mail), 1)
        for score in range(1, 6):
            self.assertIn(f"/helpdesk/csat/{csat.token}/{score}", mail.body_html)
        self.assertIn("auto-generated", mail.headers)
        # Une réponse par courriel reviendrait sur le billet.
        self.assertEqual((mail.model, mail.res_id), ("helpdesk.ticket", ticket.id))

    def test_reopened_before_send_is_cancelled(self):
        ticket = self._closed_ticket()
        ticket.stage_id = self.stage_open
        self._run(T0 + timedelta(hours=25))
        self.assertEqual(ticket.csat_ids.state, "cancelled")
        self.assertFalse(self._mail(ticket))

    def test_one_survey_per_ticket(self):
        ticket = self._closed_ticket()
        ticket.stage_id = self.stage_open
        ticket.stage_id = self.stage_closed
        self.assertEqual(len(ticket.csat_ids), 1)

    def test_no_delay_sends_at_close(self):
        self.team.csat_delay_hours = 0
        ticket = self._closed_ticket()
        self.assertEqual(ticket.csat_ids.state, "sent")
        self.assertEqual(len(self._mail(ticket)), 1)

    def test_mode_none_does_nothing(self):
        self.team.csat_mode = "none"
        ticket = self._closed_ticket()
        self.assertFalse(ticket.csat_ids)

    def test_no_address_cancels(self):
        with freeze_time(T0):
            ticket = self.Ticket.create({
                "name": "Sans courriel", "description": "<p>x</p>",
                "team_id": self.team.id, "stage_id": self.stage_open.id,
            })
            ticket.stage_id = self.stage_closed
        self._run(T0 + timedelta(hours=25))
        self.assertEqual(ticket.csat_ids.state, "cancelled")

    def test_negative_answer_opens_followup(self):
        self.team.csat_delay_hours = 0
        ticket = self._closed_ticket()
        csat = ticket.csat_ids
        csat._record_answer("2", reason="slow", ces="2",
                            comment="Trois jours pour une réponse.")
        self.assertTrue(csat.negative)
        self.assertEqual(csat.reason, "slow")
        self.assertFalse(csat.ces, "CES désactivé sur l'équipe : ignoré")
        self.assertEqual(csat.followup_state, "todo")
        self.assertEqual(csat.followup_activity_id.user_id, self.agent)
        self.assertEqual(ticket.csat_rating, "2")
        note = ticket.message_ids.filtered(
            lambda m: "Satisfaction : 2/5" in (m.body or ""))
        self.assertTrue(note)
        self.assertIn("Trois jours", note.body)

    def test_followup_user_from_team(self):
        boss = new_test_user(self.env, login="resp-csat", groups="base.group_user")
        self.team.write({"csat_delay_hours": 0, "csat_followup_user_id": boss.id})
        csat = self._closed_ticket().csat_ids
        csat._record_answer("1")
        self.assertEqual(csat.followup_activity_id.user_id, boss)

    def test_positive_answer_drops_reason_and_followup(self):
        self.team.write({"csat_delay_hours": 0, "csat_ces_enabled": True})
        csat = self._closed_ticket().csat_ids
        csat._record_answer("1", reason="unresolved")
        self.assertEqual(csat.followup_state, "todo")
        # Le client corrige vers le haut : le suivi tombe, la raison aussi.
        csat._record_answer("5", reason="unresolved", ces="5")
        self.assertFalse(csat.reason)
        self.assertEqual(csat.ces, "5")
        self.assertEqual(csat.followup_state, "none")
        self.assertFalse(csat.followup_activity_id.exists())

    def test_followup_done_when_activity_done(self):
        self.team.csat_delay_hours = 0
        csat = self._closed_ticket().csat_ids
        csat._record_answer("1")
        csat.followup_activity_id.action_feedback(feedback="Appelé, réglé.")
        self.assertEqual(csat.followup_state, "done")

    def test_expiry(self):
        self.team.csat_delay_hours = 0
        csat = self._closed_ticket().csat_ids
        self._run(T0 + timedelta(days=27))
        self.assertEqual(csat.state, "sent")
        self._run(T0 + timedelta(days=29))
        self.assertEqual(csat.state, "expired")
        self.assertFalse(csat._is_open())

    def test_auto_closed_by_reminders_is_not_surveyed(self):
        self.team.write({
            "reminder_enabled": True,
            "reminder_close_stage_id": self.stage_closed.id,
            "sla_calendar_id": False,
        })
        with freeze_time(T0):
            ticket = self.Ticket.create({
                "name": "Silence", "description": "<p>x</p>",
                "team_id": self.team.id, "stage_id": self.stage_open.id,
                "partner_id": self.client.id, "waiting_state": "client",
            })
        for day in (4, 8, 12):
            with freeze_time(T0 + timedelta(days=day)):
                self.Ticket._cron_waiting_reminders()
        self.assertTrue(ticket.reminder_auto_closed)
        self.assertFalse(ticket.csat_ids)

    def test_team_with_survey_keeps_survey_mode(self):
        survey = self.env["survey.survey"].create({"title": "Ancien CSAT"})
        model = self.env.ref("helpdesk_mgmt.model_helpdesk_ticket")
        team = self.env["helpdesk.ticket.team"].create({
            "name": "Ancien mode",
            "alias_id": self.env["mail.alias"].create({
                "alias_name": "ancien-mode", "alias_model_id": model.id,
            }).id,
            "csat_survey_id": survey.id,
        })
        self.assertEqual(team.csat_mode, "survey")


@tagged("post_install", "-at_install", "bf_helpdesk", "bf_helpdesk_csat")
class TestCsatV2Page(CsatCommon, HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_csat()
        cls.team.csat_delay_hours = 0

    def _csrf(self, html):
        return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)

    def test_click_does_not_record(self):
        csat = self._closed_ticket().csat_ids
        html = self.url_open(f"/helpdesk/csat/{csat.token}/4").text
        self.assertIn('value="4" checked', html.replace('checked="checked"', "checked"))
        csat.invalidate_recordset()
        self.assertEqual(csat.state, "sent")
        self.assertFalse(csat.rating)

    def test_submit_records_answer(self):
        csat = self._closed_ticket().csat_ids
        html = self.url_open(f"/helpdesk/csat/{csat.token}/2").text
        resp = self.url_open(f"/helpdesk/csat/{csat.token}/submit", data={
            "csrf_token": self._csrf(html),
            "rating": "2", "reason": "unresolved", "comment": "Toujours brisé.",
        })
        self.assertIn("désolés", resp.text)
        csat.invalidate_recordset()
        self.assertEqual(csat.state, "answered")
        self.assertEqual((csat.rating, csat.reason), ("2", "unresolved"))
        self.assertEqual(csat.comment, "Toujours brisé.")

    def test_submit_without_rating_is_refused(self):
        csat = self._closed_ticket().csat_ids
        html = self.url_open(f"/helpdesk/csat/{csat.token}").text
        resp = self.url_open(f"/helpdesk/csat/{csat.token}/submit", data={
            "csrf_token": self._csrf(html), "comment": "rien",
        })
        self.assertIn("Choisissez une note", resp.text)
        csat.invalidate_recordset()
        self.assertEqual(csat.state, "sent")

    def test_unknown_or_expired_token(self):
        html = self.url_open("/helpdesk/csat/pas-un-jeton/5").text
        self.assertTrue("plus actif" in html or "no longer active" in html)
        csat = self._closed_ticket().csat_ids
        csat.state = "expired"
        html = self.url_open(f"/helpdesk/csat/{csat.token}").text
        self.assertTrue("plus actif" in html or "no longer active" in html)
