from datetime import datetime, timedelta

from freezegun import freeze_time

from odoo.tests import TransactionCase, new_test_user, tagged


@tagged("bf_helpdesk", "bf_helpdesk_sla")
class TestTicketSla(TransactionCase):
    """SLA 18.0.4.6.0 : horaire d'affaires, pause, première réponse, état."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        cls.Ticket = cls.env["helpdesk.ticket"]
        model = cls.env.ref("helpdesk_mgmt.model_helpdesk_ticket")
        # Lundi au vendredi, 8 h à 12 h et 13 h à 17 h, heure de Montréal.
        cls.calendar = cls.env["resource.calendar"].create({
            "name": "SLA essai 40 h",
            "tz": "America/Toronto",
            "attendance_ids": [
                (0, 0, {"name": f"{d} {p}", "dayofweek": str(d),
                        "hour_from": h[0], "hour_to": h[1], "day_period": p})
                for d in range(5)
                for p, h in (("morning", (8, 12)), ("afternoon", (13, 17)))
            ],
        })
        cls.team_hours = cls.env["helpdesk.ticket.team"].create({
            "name": "SLA ouvré",
            "alias_id": cls.env["mail.alias"].create({
                "alias_name": "sla-ouvre", "alias_model_id": model.id,
            }).id,
            "sla_response_hours": 4.0,
            "sla_resolve_hours": 16.0,
            "sla_calendar_id": cls.calendar.id,
        })
        cls.team_civil = cls.env["helpdesk.ticket.team"].create({
            "name": "SLA civil",
            "alias_id": cls.env["mail.alias"].create({
                "alias_name": "sla-civil", "alias_model_id": model.id,
            }).id,
            "sla_response_hours": 4.0,
            "sla_resolve_hours": 48.0,
            "sla_calendar_id": False,
        })
        cls.agent = new_test_user(
            cls.env, login="sla-agent",
            groups="base.group_user,helpdesk_mgmt.group_helpdesk_user_team",
        )
        (cls.team_hours | cls.team_civil).write({
            "user_ids": [(4, cls.agent.id)],
        })
        cls.customer = cls.env["res.partner"].create({
            "name": "Client SLA", "email": "client-sla@example.com",
        })

    def _ticket(self, team, created=None):
        ticket = self.Ticket.create({
            "name": "billet SLA",
            "description": "<p>x</p>",
            "team_id": team.id,
            "partner_id": self.customer.id,
        })
        if created:
            self.env.cr.execute(
                "UPDATE helpdesk_ticket SET create_date = %s WHERE id = %s",
                (created, ticket.id),
            )
            ticket.invalidate_recordset(["create_date"])
            self._recompute(ticket)
        return ticket

    def _recompute(self, ticket):
        for name in ("sla_response_deadline", "sla_resolve_deadline", "sla_state"):
            self.env.add_to_compute(self.Ticket._fields[name], ticket)
        ticket.flush_recordset()
        ticket.invalidate_recordset()

    # ------------------------------------------------ horaire d'affaires

    def test_deadline_skips_weekend(self):
        # Vendredi 25 septembre 2026, 16 h à Montréal (20 h UTC).
        ticket = self._ticket(self.team_hours, datetime(2026, 9, 25, 20, 0))
        # 1 h vendredi, puis 3 h lundi : lundi 11 h à Montréal, 15 h UTC.
        self.assertEqual(ticket.sla_response_deadline, datetime(2026, 9, 28, 15, 0))

    def test_deadline_respects_calendar_leave(self):
        self.env["resource.calendar.leaves"].create({
            "name": "Congé essai",
            "calendar_id": self.calendar.id,
            "date_from": datetime(2026, 9, 28, 4, 0),
            "date_to": datetime(2026, 9, 29, 3, 59),
        })
        ticket = self._ticket(self.team_hours, datetime(2026, 9, 25, 20, 0))
        # Lundi chômé : mardi 11 h à Montréal.
        self.assertEqual(ticket.sla_response_deadline, datetime(2026, 9, 29, 15, 0))

    def test_civil_hours_without_calendar(self):
        ticket = self._ticket(self.team_civil, datetime(2026, 9, 25, 20, 0))
        self.assertEqual(ticket.sla_response_deadline, datetime(2026, 9, 26, 0, 0))

    def test_new_team_defaults_to_company_calendar(self):
        team = self.env["helpdesk.ticket.team"].create({"name": "Défaut"})
        self.assertEqual(team.sla_calendar_id, self.env.company.resource_calendar_id)

    # ------------------------------------------------ première réponse

    def test_internal_note_is_not_first_response(self):
        ticket = self._ticket(self.team_civil)
        ticket.with_user(self.agent).message_post(
            body="note interne", message_type="comment",
            subtype_xmlid="mail.mt_note",
        )
        self.assertFalse(ticket.first_response_date)

    def test_customer_message_is_not_first_response(self):
        ticket = self._ticket(self.team_civil)
        ticket.message_post(
            body="relance du client", message_type="comment",
            subtype_xmlid="mail.mt_comment", author_id=self.customer.id,
        )
        self.assertFalse(ticket.first_response_date)

    def test_public_staff_reply_sets_first_response_once(self):
        ticket = self._ticket(self.team_civil)
        ticket.with_user(self.agent).message_post(
            body="réponse", message_type="comment",
            subtype_xmlid="mail.mt_comment",
        )
        first = ticket.first_response_date
        self.assertTrue(first)
        with freeze_time(first + timedelta(hours=3)):
            ticket.with_user(self.agent).message_post(
                body="suite", message_type="comment",
                subtype_xmlid="mail.mt_comment",
            )
        self.assertEqual(ticket.first_response_date, first)

    # ------------------------------------------------ pause

    def test_waiting_client_pauses_resolution(self):
        created = datetime(2026, 9, 21, 12, 0)
        ticket = self._ticket(self.team_civil, created)
        self.assertEqual(ticket.sla_resolve_deadline, created + timedelta(hours=48))
        with freeze_time(created + timedelta(hours=1)):
            ticket.with_user(self.agent).message_post(
                body="question au client", message_type="comment",
                subtype_xmlid="mail.mt_comment",
            )
        with freeze_time(created + timedelta(hours=5)):
            ticket.waiting_state = "client"
            self.assertTrue(ticket.sla_paused_since)
            self._recompute(ticket)
            self.assertEqual(ticket.sla_state, "paused")
        with freeze_time(created + timedelta(hours=15)):
            ticket.waiting_state = False
        self.assertAlmostEqual(ticket.sla_paused_hours, 10.0, places=2)
        self.assertFalse(ticket.sla_paused_since)
        self.assertEqual(ticket.sla_resolve_deadline, created + timedelta(hours=58))

    def test_waiting_external_does_not_pause(self):
        ticket = self._ticket(self.team_civil)
        ticket.waiting_state = "external"
        self.assertFalse(ticket.sla_paused_since)

    # ------------------------------------------------ état

    def test_state_ok_then_at_risk_then_breached(self):
        created = datetime(2026, 9, 21, 12, 0)
        ticket = self._ticket(self.team_civil, created)
        with freeze_time(created + timedelta(hours=1)):
            self._recompute(ticket)
            self.assertEqual(ticket.sla_state, "ok")
        with freeze_time(created + timedelta(hours=3, minutes=30)):
            self._recompute(ticket)
            self.assertEqual(ticket.sla_state, "at_risk")
        with freeze_time(created + timedelta(hours=5)):
            self._recompute(ticket)
            self.assertEqual(ticket.sla_state, "breached")
            self.assertTrue(ticket.sla_response_breach)

    def test_late_response_stays_breached(self):
        created = datetime(2026, 9, 21, 12, 0)
        ticket = self._ticket(self.team_civil, created)
        with freeze_time(created + timedelta(hours=6)):
            ticket.with_user(self.agent).message_post(
                body="en retard", message_type="comment",
                subtype_xmlid="mail.mt_comment",
            )
            self._recompute(ticket)
            self.assertEqual(ticket.sla_state, "breached")
            # Le bandeau appelle un geste : il tombe une fois la réponse faite.
            self.assertFalse(ticket.sla_response_breach)

    def test_cron_refreshes_state_and_schedules_activity(self):
        created = datetime(2026, 9, 21, 12, 0)
        ticket = self._ticket(self.team_civil, created)
        with freeze_time(created + timedelta(hours=1)):
            self._recompute(ticket)
        self.assertEqual(ticket.sla_state, "ok")
        with freeze_time(created + timedelta(hours=5)):
            self.Ticket._cron_sla_breach_activity()
        ticket.invalidate_recordset()
        self.assertEqual(ticket.sla_state, "breached")
        activity = self.env["mail.activity"].search([
            ("res_model", "=", "helpdesk.ticket"),
            ("res_id", "=", ticket.id),
            ("summary", "=", "SLA dépassé"),
        ])
        self.assertEqual(len(activity), 1)

    # ------------------------------------------------ droits

    def test_agent_can_use_macro_wizard(self):
        ticket = self._ticket(self.team_civil)
        macro = self.env["helpdesk.macro"].create({
            "name": "Merci", "body_html": "<p>Merci.</p>",
        })
        wizard = self.env["helpdesk.macro.apply.wizard"].with_user(self.agent).create({
            "ticket_id": ticket.id, "macro_id": macro.id,
        })
        wizard.action_apply()
