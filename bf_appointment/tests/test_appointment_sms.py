import re
from datetime import timedelta
from unittest.mock import patch

from odoo import Command, fields
from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged

from ..models import _sms_text
from ..models import resource_booking as rb_module


def _fake_normalize_na(number):
    """The real bf_securetransfer normalizer, reproduced so the fake transport
    accepts and rejects exactly what production would."""
    digits = re.sub(r"\D", "", number or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits if len(digits) == 10 else None


class FakeSmsApi:
    """Stand-in for bf_securetransfer.models.sms.

    Records every send so a test can assert not just the outcome but how many
    times the transport was reached — the point of the anti-hammering guard.
    """

    def __init__(self, configured=True, results=None):
        self._configured = configured
        self.results = list(results or [])
        self.sends = []

    def configured(self, env):
        return self._configured

    def normalize_na(self, number):
        return _fake_normalize_na(number)

    def send(self, env, dst, message):
        self.sends.append((dst, message))
        if self.results:
            return self.results.pop(0)
        return True


@tagged("bf_appointment", "bf_appointment_sms")
class TestAppointmentSms(TransactionCase):
    """The reminder SMS channel: it must add a delivery path without ever
    subtracting one. Every failure mode has to land on e-mail."""

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
            "name": "24/7 SMS Test",
            "attendance_ids": attendances,
            "tz": "UTC",
        })
        cls.resource = cls.env["resource.resource"].create({
            "name": "SMS test material",
            "calendar_id": cls.calendar.id,
            "resource_type": "material",
            "tz": "UTC",
        })
        cls.combination = cls.env["resource.booking.combination"].create({
            "resource_ids": [Command.set([cls.resource.id])],
        })
        cls.booking_type = cls.env["resource.booking.type"].create({
            "name": "SMS Test Type",
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
            "name": "BF Appointment SMS Test",
            "model_id": cls.env["ir.model"]._get_id("resource.booking"),
            "subject": "Reminder",
            "body_html": "<p>Reminder</p>",
        })

    def _partner(self, phone=None, mobile=None, name="SMS Booker"):
        return self.env["res.partner"].create({
            "name": name,
            "email": "sms-booker@test.invalid",
            "phone": phone or False,
            "mobile": mobile or False,
        })

    def _schedule(self, channel="sms", sms_body="Rappel: rendez-vous demain."):
        return self.env["appointment.email.schedule"].create({
            "type_id": self.booking_type.id,
            "trigger": "before",
            # A very wide window keeps send_at safely in the past while the
            # booking itself stays in the future.
            "hours": 24 * 365,
            "template_id": self.template.id,
            "channel": channel,
            "sms_body": sms_body if channel in ("sms", "both") else False,
        })

    def _booking(self, partner, hours_out=1):
        """A confirmed booking ``hours_out`` in the future.

        The offset is a parameter because the single test resource can only
        hold one booking per slot: two bookings at the same hour collide on
        "all resources are busy" long before the cron ever sees them.
        """
        booking = self.env["resource.booking"].create({
            "partner_ids": [(6, 0, [partner.id])],
            "type_id": self.booking_type.id,
            "combination_id": self.combination.id,
            "combination_auto_assign": False,
            "start": fields.Datetime.now() + timedelta(hours=hours_out),
            "duration": 1.0,
        })
        booking.state = "confirmed"
        return booking

    def _run_cron(self, fake_api):
        Booking = self.env["resource.booking"]
        with patch.object(rb_module, "sms_api", fake_api), \
                patch.object(
                    type(Booking), "_send_appointment_email", autospec=True
                ) as mock_email:
            Booking._cron_send_appointment_emails()
        return mock_email

    # ---- the default must not move ----

    def test_email_channel_never_touches_sms(self):
        """An existing schedule (channel defaults to email) is untouched."""
        schedule = self._schedule(channel="email")
        self.assertEqual(schedule.channel, "email")
        self._booking(self._partner(mobile="514-555-1234"))
        api = FakeSmsApi()
        mock_email = self._run_cron(api)
        self.assertEqual(api.sends, [], "an e-mail schedule must not send SMS")
        self.assertEqual(mock_email.call_count, 1)

    # ---- the happy path ----

    def test_sms_channel_sends_sms_and_skips_email(self):
        self._schedule(channel="sms")
        self._booking(self._partner(mobile="(514) 555-1234"))
        api = FakeSmsApi()
        mock_email = self._run_cron(api)
        self.assertEqual(len(api.sends), 1)
        self.assertEqual(api.sends[0][0], "5145551234", "number must be normalized")
        self.assertEqual(
            mock_email.call_count, 0,
            "channel 'sms' that actually sent must not also e-mail",
        )

    def test_both_channel_sends_sms_and_email(self):
        self._schedule(channel="both")
        self._booking(self._partner(mobile="5145551234"))
        api = FakeSmsApi()
        mock_email = self._run_cron(api)
        self.assertEqual(len(api.sends), 1)
        self.assertEqual(mock_email.call_count, 1)

    def test_mobile_wins_over_phone(self):
        self._schedule(channel="sms")
        self._booking(self._partner(phone="514-111-1111", mobile="514-222-2222"))
        api = FakeSmsApi()
        self._run_cron(api)
        self.assertEqual(api.sends[0][0], "5142222222")

    # ---- every failure lands on e-mail ----

    def test_no_phone_falls_back_to_email(self):
        """The public form leaves the phone optional: this is the common case."""
        self._schedule(channel="sms")
        self._booking(self._partner())
        api = FakeSmsApi()
        mock_email = self._run_cron(api)
        self.assertEqual(api.sends, [], "nothing to send to")
        self.assertEqual(mock_email.call_count, 1, "booker must still be reminded")

    def test_unusable_phone_falls_back_to_email(self):
        self._schedule(channel="sms")
        self._booking(self._partner(phone="poste 4412"))
        api = FakeSmsApi()
        mock_email = self._run_cron(api)
        self.assertEqual(api.sends, [])
        self.assertEqual(mock_email.call_count, 1)

    def test_refused_sms_falls_back_to_email(self):
        self._schedule(channel="sms")
        self._booking(self._partner(mobile="5145551234"))
        api = FakeSmsApi(results=[False])
        mock_email = self._run_cron(api)
        self.assertEqual(len(api.sends), 1, "it was attempted")
        self.assertEqual(mock_email.call_count, 1, "and it fell back")

    def test_transport_absent_falls_back_to_email(self):
        """bf_securetransfer is not a dependency; without it, e-mail carries."""
        self._schedule(channel="sms")
        self._booking(self._partner(mobile="5145551234"))
        Booking = self.env["resource.booking"]
        with patch.object(rb_module, "sms_api", None), \
                patch.object(
                    type(Booking), "_send_appointment_email", autospec=True
                ) as mock_email:
            Booking._cron_send_appointment_emails()
        self.assertEqual(mock_email.call_count, 1)

    def test_transport_not_configured_falls_back_to_email(self):
        self._schedule(channel="sms")
        self._booking(self._partner(mobile="5145551234"))
        api = FakeSmsApi(configured=False)
        mock_email = self._run_cron(api)
        self.assertEqual(api.sends, [])
        self.assertEqual(mock_email.call_count, 1)

    def test_sms_exception_does_not_sink_the_reminder(self):
        self._schedule(channel="sms")
        self._booking(self._partner(mobile="5145551234"))

        class Exploding(FakeSmsApi):
            def send(self, env, dst, message):
                raise RuntimeError("boom")

        mock_email = self._run_cron(Exploding())
        self.assertEqual(mock_email.call_count, 1)

    # ---- not hammering a refusing account ----

    def test_refusal_stands_down_for_the_rest_of_the_run(self):
        """The ~27/day cap is a quota that only drains at midnight, so once
        VoIP.ms refuses, retrying the batch would just risk the account."""
        self._schedule(channel="sms")
        for i in range(3):
            self._booking(
                self._partner(mobile="514-555-00%02d" % i, name=f"B{i}"),
                hours_out=i + 1,
            )
        api = FakeSmsApi(results=[False, True, True])
        mock_email = self._run_cron(api)
        self.assertEqual(
            len(api.sends), 1,
            "after a refusal the transport must not be reached again this run",
        )
        self.assertEqual(
            mock_email.call_count, 3, "all three bookers still get reminded"
        )

    def test_no_phone_does_not_stand_down_the_run(self):
        """A booker without a number says nothing about VoIP.ms' health —
        the next booking must still get its SMS."""
        self._schedule(channel="sms")
        self._booking(self._partner(name="no-phone"), hours_out=1)
        self._booking(self._partner(mobile="5145559999", name="has-phone"), hours_out=2)
        api = FakeSmsApi()
        mock_email = self._run_cron(api)
        self.assertEqual(len(api.sends), 1, "the reachable booker still gets an SMS")
        self.assertEqual(api.sends[0][0], "5145559999")
        self.assertEqual(mock_email.call_count, 1, "only the unreachable one e-mails")

    def test_per_run_budget_overflows_to_email(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_appointment.sms_max_per_run", "1"
        )
        self._schedule(channel="sms")
        self._booking(self._partner(mobile="5145550001", name="B0"), hours_out=1)
        self._booking(self._partner(mobile="5145550002", name="B1"), hours_out=2)
        api = FakeSmsApi()
        mock_email = self._run_cron(api)
        self.assertEqual(len(api.sends), 1, "budget of 1 means one SMS")
        self.assertEqual(mock_email.call_count, 1, "the overflow leaves by e-mail")

    # ---- authoring guards ----

    def test_sms_body_required_for_sms_channel(self):
        with self.assertRaises(ValidationError):
            self._schedule(channel="sms", sms_body="   ")

    def test_non_gsm7_body_refused(self):
        """ç is not GSM-7 even though é is — the trap this guard exists for."""
        with self.assertRaises(ValidationError):
            self._schedule(channel="sms", sms_body="Rappel: séance reçue demain.")

    def test_gsm7_accents_accepted(self):
        schedule = self._schedule(channel="sms", sms_body="Rappel: réunion prévue à 14h.")
        self.assertTrue(schedule.id)

    def test_overlong_body_refused(self):
        with self.assertRaises(ValidationError):
            self._schedule(channel="sms", sms_body="a" * 151)

    def test_rendered_body_over_budget_falls_back_to_email(self):
        """The stored body passes; what renders from it may not."""
        self._schedule(channel="sms", sms_body="Rappel {{ object.name }}")
        booking = self._booking(self._partner(mobile="5145551234"))
        booking.name = "x" * 200
        api = FakeSmsApi()
        mock_email = self._run_cron(api)
        self.assertEqual(api.sends, [], "an over-budget render must not be sent")
        self.assertEqual(mock_email.call_count, 1)


@tagged("bf_appointment", "bf_appointment_sms")
class TestSmsTextHelpers(TransactionCase):
    """The GSM-7 rules are asymmetric enough to be worth pinning down."""

    def test_gsm7_accents_are_one_septet(self):
        self.assertEqual(_sms_text.septet_len("éàÇÉäöñü"), 8)

    def test_extension_chars_cost_two(self):
        self.assertEqual(_sms_text.septet_len("€"), 2)
        self.assertEqual(_sms_text.septet_len("[]"), 4)

    def test_ucs2_chars_return_none(self):
        for char in "çêâîôûëï«»…—":
            self.assertIsNone(
                _sms_text.septet_len(char), f"{char!r} should be flagged non-GSM-7"
            )

    def test_check_reports_the_offending_chars(self):
        error = _sms_text.check("reçu")
        self.assertIn("ç", error)

    def test_check_accepts_a_realistic_reminder(self):
        self.assertIsNone(_sms_text.check(
            "Rappel: votre rendez-vous est demain a 14h30. Repondez a ce "
            "message pour annuler."
        ))

    def test_boundary_is_150_not_160(self):
        self.assertIsNone(_sms_text.check("a" * 150))
        self.assertIsNotNone(_sms_text.check("a" * 151))
