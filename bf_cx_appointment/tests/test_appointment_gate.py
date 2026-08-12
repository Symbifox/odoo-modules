"""Post-appointment feedback: the switch and the backfill window.

This bridge is cron-driven and searches the booking history, so turning
the switch on must never blast the whole archive. FEEDBACK_LOOKBACK_DAYS
is what stands between "ask the people we just met" and "mail every
client we ever booked": it gets its own test.
"""
from datetime import timedelta

from odoo import fields
from odoo.tests import tagged

from odoo.addons.bf_cx.tests.common import CxBridgeCase
from odoo.addons.bf_cx_appointment.models.resource_booking import (
    FEEDBACK_LOOKBACK_DAYS,
)


@tagged("post_install", "-at_install")
class TestAppointmentFeedbackGate(CxBridgeCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.booking_type = cls.env["resource.booking.type"].create(
            {"name": "Type pont CX"}
        )
        # A scheduled booking must have booked some resource
        # (resource_booking._check_scheduling). The calendar-fit half of
        # that constraint is skipped for bookings that already happened,
        # which is exactly what this bridge looks at - so a bare resource
        # is enough.
        resource = cls.env["resource.resource"].create(
            {"name": "Ressource pont CX"}
        )
        cls.combination = cls.env["resource.booking.combination"].create(
            {"resource_ids": [(6, 0, resource.ids)]}
        )
        cls.booking_type.combination_rel_ids = [
            (0, 0, {"combination_id": cls.combination.id})
        ]
        cls.Booking = cls.env["resource.booking"]

    def _make_booking(self, finished_days_ago=1):
        stop = fields.Datetime.now() - timedelta(days=finished_days_ago)
        return self.Booking.create(
            {
                "type_id": self.booking_type.id,
                "combination_id": self.combination.id,
                "combination_auto_assign": False,
                "partner_ids": [(6, 0, self.cx_partner.ids)],
                "start": stop - timedelta(hours=1),
                "stop": stop,
            }
        )

    def test_gate_off_sends_nothing(self):
        self.set_gate("bf_cx.appointment_feedback", False)
        booking = self._make_booking()
        self.Booking._bf_cx_request_post_booking_feedback()
        self.assertNothingSent(booking)
        self.assertFalse(booking.bf_cx_feedback_requested)

    def test_gate_on_asks_once(self):
        self.set_gate("bf_cx.appointment_feedback", True)
        booking = self._make_booking()
        booking.state = "confirmed"
        self.Booking._bf_cx_request_post_booking_feedback()
        self.assertEqual(len(self.ratings_of(booking)), 1)
        self.assertTrue(booking.bf_cx_feedback_requested)
        self.Booking._bf_cx_request_post_booking_feedback()
        self.assertEqual(len(self.ratings_of(booking)), 1)

    def test_old_bookings_are_out_of_the_window(self):
        """Switching the feature on must not backfill the archive."""
        self.set_gate("bf_cx.appointment_feedback", True)
        stale = self._make_booking(
            finished_days_ago=FEEDBACK_LOOKBACK_DAYS + 3
        )
        stale.state = "confirmed"
        self.Booking._bf_cx_request_post_booking_feedback()
        self.assertFalse(self.ratings_of(stale))
        self.assertFalse(stale.bf_cx_feedback_requested)

    def test_cooldown_defers_rather_than_burns_the_slot(self):
        """A blocked booking must stay eligible for a later tick.

        The flag is only set after a successful send: otherwise the
        cooldown would permanently consume the one feedback slot the
        booking had.
        """
        self.Param.set_param("bf_cx.solicitation_cooldown_days", "30")
        self.set_gate("bf_cx.appointment_feedback", True)
        booking = self._make_booking()
        booking.state = "confirmed"
        self.cx_partner._bf_cx_mark_solicited()
        self.Booking._bf_cx_request_post_booking_feedback()
        self.assertFalse(self.ratings_of(booking))
        self.assertFalse(
            booking.bf_cx_feedback_requested,
            "a deferred booking must be retried once the cooldown expires",
        )
        # Cooldown expired: the later tick does send.
        self.cx_partner.bf_cx_last_solicited = False
        self.Booking._bf_cx_request_post_booking_feedback()
        self.assertEqual(len(self.ratings_of(booking)), 1)
