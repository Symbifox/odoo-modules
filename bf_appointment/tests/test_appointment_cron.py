from datetime import timedelta
from unittest.mock import patch

from odoo import Command, fields
from odoo.tests import TransactionCase, tagged


@tagged("bf_appointment", "bf_appointment_cron")
class TestAppointmentCronDedup(TransactionCase):
    """Regression: the cron must not resend an already-sent scheduled email
    when it fires again inside the same transaction (M2M cache staleness)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True, tz="UTC"))
        attendances = [
            Command.create({
                "name": f"All day {d}",
                "dayofweek": str(d),
                "hour_from": 0.0,
                "hour_to": 24.0,
                "day_period": "morning",
            })
            for d in range(7)
        ]
        cls.calendar = cls.env["resource.calendar"].create({
            "name": "24/7 Test",
            "attendance_ids": attendances,
            "tz": "UTC",
        })
        cls.resource = cls.env["resource.resource"].create({
            "name": "Test material",
            "calendar_id": cls.calendar.id,
            "resource_type": "material",
            "tz": "UTC",
        })
        cls.combination = cls.env["resource.booking.combination"].create({
            "resource_ids": [Command.set([cls.resource.id])],
        })
        cls.partner = cls.env["res.partner"].create({
            "name": "Test Booker",
            "email": "booker@test.invalid",
        })
        cls.booking_type = cls.env["resource.booking.type"].create({
            "name": "Test Type",
            "duration": 1.0,
            "slot_duration": 1.0,
            "modifications_deadline": 0.0,
            "combination_assignment": "sorted",
            "resource_calendar_id": cls.calendar.id,
            "combination_rel_ids": [
                Command.create({"sequence": 0, "combination_id": cls.combination.id}),
            ],
        })
        cls.template = cls.env["mail.template"].create({
            "name": "BF Appointment Reminder Test",
            "model_id": cls.env["ir.model"]._get_id("resource.booking"),
            "subject": "Reminder",
            "body_html": "<p>Reminder</p>",
        })
        # trigger="before" with a very large window so send_at is comfortably in
        # the past regardless of when the test runs, keeps the booking's start
        # in the future (avoiding OCA scheduling constraints).
        cls.schedule = cls.env["appointment.email.schedule"].create({
            "type_id": cls.booking_type.id,
            "trigger": "before",
            "hours": 24 * 365,
            "template_id": cls.template.id,
        })

    def _make_confirmed_booking(self):
        booking = self.env["resource.booking"].create({
            "partner_ids": [(6, 0, [self.partner.id])],
            "type_id": self.booking_type.id,
            "combination_id": self.combination.id,
            "combination_auto_assign": False,
            "start": fields.Datetime.now() + timedelta(hours=1),
            "duration": 1.0,
        })
        booking.state = "confirmed"
        return booking

    def test_cron_dedup_same_transaction(self):
        """Calling the cron twice in one transaction must send only once per (booking, schedule)."""
        booking = self._make_confirmed_booking()
        Booking = self.env["resource.booking"]
        with patch.object(
            type(booking), "_send_appointment_email", autospec=True
        ) as mock_send:
            Booking._cron_send_appointment_emails()
            self.assertEqual(
                mock_send.call_count, 1,
                "First cron pass should send exactly one scheduled email",
            )
            self.assertIn(
                self.schedule, booking.sent_schedule_ids,
                "Schedule must be recorded as sent after first pass",
            )
            Booking._cron_send_appointment_emails()
            self.assertEqual(
                mock_send.call_count, 1,
                "Second cron pass must not resend (M2M cache invalidation regression)",
            )

    def test_cron_skips_when_send_window_not_reached(self):
        """A schedule whose send_at is still in the future must not fire yet."""
        # trigger="before", hours=1h, start 2h away → send_at = now+1h, should not send
        near_schedule = self.env["appointment.email.schedule"].create({
            "type_id": self.booking_type.id,
            "trigger": "before",
            "hours": 1.0,
            "template_id": self.template.id,
        })
        self.schedule.active = False
        booking = self._make_confirmed_booking()
        booking.start = fields.Datetime.now() + timedelta(hours=2)
        Booking = self.env["resource.booking"]
        with patch.object(
            type(booking), "_send_appointment_email", autospec=True
        ) as mock_send:
            Booking._cron_send_appointment_emails()
            self.assertEqual(mock_send.call_count, 0)
            self.assertNotIn(near_schedule, booking.sent_schedule_ids)

    def test_cron_before_trigger_does_not_fire_after_start(self):
        """A 'before' schedule must never fire once the booking has already started.

        Regression guard: without the `now < booking.start` cap, a 24h reminder
        on a booking that started 5 minutes ago would still fire (send_at was
        23h55m ago, firmly past). Users received "Rappel demain" AFTER the
        meeting had already happened.
        """
        late_schedule = self.env["appointment.email.schedule"].create({
            "type_id": self.booking_type.id,
            "trigger": "before",
            "hours": 24.0,
            "template_id": self.template.id,
        })
        self.schedule.active = False
        booking = self._make_confirmed_booking()
        # Booking started 5 minutes ago, well past the send_at of 23h55m ago
        booking.start = fields.Datetime.now() - timedelta(minutes=5)
        Booking = self.env["resource.booking"]
        with patch.object(
            type(booking), "_send_appointment_email", autospec=True
        ) as mock_send:
            Booking._cron_send_appointment_emails()
            self.assertEqual(
                mock_send.call_count, 0,
                "'before' schedule must not fire after booking start",
            )
            self.assertNotIn(late_schedule, booking.sent_schedule_ids)
