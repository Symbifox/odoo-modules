import itertools
from datetime import datetime, time, timedelta, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged
from odoo.tools import mute_logger

from .common import WorkHoursCase, acting_as

MTL = ZoneInfo("America/Montreal")


def local(days_ago, hour=12, minute=0):
    """Naive UTC for a Montreal wall-clock time, ``days_ago`` days before today."""
    day = datetime.now(MTL).date() - timedelta(days=days_ago)
    moment = datetime.combine(day, time(hour, minute), tzinfo=MTL)
    return moment.astimezone(timezone.utc).replace(tzinfo=None)


def local_date(days_ago):
    return datetime.now(MTL).date() - timedelta(days=days_ago)


@tagged("post_install", "-at_install")
class TestEarlierDays(WorkHoursCase):
    """« My earlier days »: a consent of its own, read back as far as the person chose."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ICP = cls.env["ir.config_parameter"].sudo()
        ICP.set_param("bf_work_hours.gap_minutes", 15)
        # A short retention keeps « all the history kept » small enough to compute here.
        ICP.set_param("bf_work_hours.day_retention_days", 45)

    def setUp(self):
        super().setUp()
        # The job goes through every live choice in the database: only this test's count.
        self.Consent.sudo().search([("scope", "=", "history"), ("withdrawn_at", "=", False)]).write(
            {"withdrawn_at": fields.Datetime.now()})
        # Hours on two days ago, at 9:00 in Montreal.
        self.since = local(2, 9)
        self.activate(self.alice, granted_at=self.since)

    def meeting(self, start, hours=1.0, partners=None):
        partners = partners or [self.alice.partner_id, self.bob.partner_id]
        event = self.env["calendar.event"].sudo().create({
            "name": "WH earlier", "start": start, "stop": start + timedelta(hours=hours),
            "partner_ids": [(6, 0, [p.id for p in partners])], "show_as": "busy",
        })
        event.attendee_ids.write({"state": "accepted"})
        return event

    def take_in(self, user, span="30", scope="history"):
        wizard = self.env["bf.work.activate.wizard"].with_user(user).create(
            {"scope": scope, "understood": True, "history_span": span})
        wizard.action_confirm()
        return self.Consent.sudo().search([("user_id", "=", user.id), ("scope", "=", "history"),
                                           ("withdrawn_at", "=", False)])

    def run_job(self):
        with patch.object(self.env.cr, "commit"):
            self.Day._cron_bf_work_history()

    def days(self, user):
        return self.Day.sudo().search([("user_id", "=", user.id)])

    # ------------------------------------------------------------------

    def test_off_by_default_and_never_read_before_the_hours(self):
        self.meeting(local(10, 14))
        self.Day._bf_compute(self.alice, [local_date(10)])
        self.assertFalse(self.days(self.alice), "Without the earlier days, nothing before the activation.")

    def test_it_needs_the_hours(self):
        with self.assertRaises(UserError):
            self.take_in(self.bob)
        with self.assertRaises(UserError):
            self.Consent.with_user(self.bob).create({"scope": "history", "history_span": "30"})

    def test_the_span_and_the_start_are_the_servers(self):
        consent = self.take_in(self.alice)
        expected = fields.Datetime.now() - timedelta(days=30)
        self.assertLess(abs((consent.read_from - expected).total_seconds()), 60)
        self.assertEqual(consent.history_span, "30")
        self.assertIn("o_bf_work_notice_history", consent.notice_text)
        self.assertEqual(consent.notice_version, "3")
        self.withdraw(self.alice, "history")
        # A start sent by the caller, in vals or as a context default, never reaches the record.
        Consent = self.Consent.with_user(self.alice).with_context(
            default_read_from="2000-01-01 00:00:00", default_computed_from="2000-01-01")
        consent = Consent.create({"scope": "history", "history_span": "30", "read_from": "2000-01-01 00:00:00",
                                  "computed_from": "2000-01-01"})
        self.assertLess(abs((consent.read_from - expected).total_seconds()), 60)
        self.assertFalse(consent.computed_from)
        # « All the history kept » = as far as a day is kept.
        self.withdraw(self.alice, "history")
        consent = self.take_in(self.alice, "all")
        self.assertLess(abs((consent.read_from - (fields.Datetime.now() - timedelta(days=45))).total_seconds()), 60)

    def test_a_span_must_be_chosen(self):
        for span in ("none", False):
            with self.assertRaises(UserError):
                self.take_in(self.alice, span)
        with self.assertRaises(UserError):
            self.Consent.with_user(self.alice).create({"scope": "history"})
        with self.assertRaises(ValueError):
            self.Consent.with_user(self.alice).create({"scope": "history", "history_span": "3650"})
        self.assertFalse(self.Consent.sudo().search([("user_id", "=", self.alice.id), ("scope", "=", "history")]))

    def test_the_hours_carry_no_start(self):
        """A start on another scope would be evidence of nothing: it is never written."""
        consent = self.Consent.sudo().search([("user_id", "=", self.alice.id), ("scope", "=", "hours")])
        self.assertFalse(consent.read_from or consent.history_span)
        Consent = self.Consent.with_user(self.alice).with_context(default_read_from="2000-01-01 00:00:00",
                                                                   default_history_span="all")
        self.activate(self.alice, "insights")
        insights = Consent.sudo().search([("user_id", "=", self.alice.id), ("scope", "=", "insights")])
        self.assertFalse(insights.read_from or insights.history_span)
        self.withdraw(self.alice, "insights")
        insights = Consent.create({"scope": "insights"})
        self.assertFalse(insights.read_from or insights.history_span)

    def test_only_the_person_in_their_own_session(self):
        Consent = self.Consent.with_user(self.admin)
        with self.assertRaises(AccessError):
            Consent.create({"user_id": self.alice.id, "scope": "history", "history_span": "30"})
        with acting_as(self.alice.id, path="/xmlrpc/2/object"), self.assertRaises(AccessError):
            self.Consent.with_user(self.alice).create({"scope": "history", "history_span": "30"})
        with acting_as(self.alice.id, bf_impersonate=99), self.assertRaises(AccessError):
            self.Consent.with_user(self.alice).create({"scope": "history", "history_span": "30"})
        self.assertFalse(self.Consent.sudo().search([("user_id", "=", self.alice.id), ("scope", "=", "history")]))

    def test_the_job_takes_in_the_earlier_days(self):
        self.meeting(local(10, 14))
        self.meeting(local(40, 14))                     # beyond 30 days: not taken in
        self.take_in(self.alice)
        self.run_job()
        days = self.days(self.alice)
        self.assertIn(local_date(10), days.mapped("date"))
        self.assertAlmostEqual(days.filtered(lambda d: d.date == local_date(10)).hours, 1.0, places=2)
        self.assertNotIn(local_date(40), days.mapped("date"))
        self.assertGreaterEqual(min(days.mapped("date")), local_date(30))
        consent = self.Consent.sudo().search([("user_id", "=", self.alice.id), ("scope", "=", "history")])
        self.assertLessEqual(consent.computed_from, local_date(30) + timedelta(days=1))
        # The job is done: a second run computes nothing more.
        before = consent.computed_from
        self.run_job()
        self.assertEqual(consent.computed_from, before)

    def test_all_the_history_kept_and_by_slices(self):
        self.meeting(local(40, 14))
        self.take_in(self.alice, "all")
        with patch("odoo.addons.bf_work_hours.models.work_day.HISTORY_RUN_SECONDS", 0):
            self.run_job()                               # out of time at once: nothing done
        self.assertNotIn(local_date(40), self.days(self.alice).mapped("date"))
        self.run_job()
        self.assertIn(local_date(40), self.days(self.alice).mapped("date"))
        self.assertGreaterEqual(min(self.days(self.alice).mapped("date")), local_date(45))

    def test_never_beyond_the_days_kept(self):
        """A day older than the retention would be purged at the next run: it is not taken in."""
        self.meeting(local(30, 14))
        self.take_in(self.alice, "all")                   # 45 days back
        self.env["ir.config_parameter"].sudo().set_param("bf_work_hours.day_retention_days", 20)
        self.run_job()
        dates = self.days(self.alice).mapped("date")
        self.assertNotIn(local_date(30), dates)
        self.assertGreaterEqual(min(dates), local_date(21))

    def test_the_job_reports_its_progress(self):
        self.take_in(self.alice, "all")
        with patch("odoo.addons.bf_work_hours.models.work_day.HISTORY_SLICE_DAYS", 10), \
                patch("odoo.addons.bf_work_hours.models.work_day.HISTORY_RUN_SECONDS", 0), \
                patch.object(type(self.env["ir.cron"]), "_notify_progress") as progress:
            self.run_job()
        self.assertEqual(progress.call_args.kwargs["done"], 0)
        self.assertGreater(progress.call_args.kwargs["remaining"], 40)

    def test_one_failure_does_not_stop_the_others(self):
        self.activate(self.bob, granted_at=self.since)
        self.meeting(local(10, 14))
        self.take_in(self.alice)
        self.take_in(self.bob)
        original = type(self.Day)._bf_compute

        def boom(model, user, dates):
            if user == self.alice:
                raise ValueError("boom")
            return original(model, user, dates)

        with patch.object(type(self.Day), "_bf_compute", boom), \
                mute_logger("odoo.addons.bf_work_hours.models.work_day"):
            self.run_job()
        self.assertIn(local_date(10), self.days(self.bob).mapped("date"))

    def test_live_sources_never_reach_before_the_hours(self):
        """Browser activity and the timer start with the hours, earlier days or not."""
        self.signal(self.alice, local(10, 9), 60)                       # a presence from before: forged
        self.signal(self.alice, self.since - timedelta(minutes=30), 60)  # straddles the activation
        self.take_in(self.alice)
        self.run_job()
        self.assertFalse(self.days(self.alice).filtered(lambda d: d.date == local_date(10)).hours)
        day = self.days(self.alice).filtered(lambda d: d.date == local_date(2))
        self.assertAlmostEqual(day.source_hours.get("presence", 0), 0.5, places=2)

    def test_indicators_follow_the_earlier_days(self):
        self.activate(self.alice, "insights", granted_at=self.since)
        self.meeting(local(10, 14))
        self.take_in(self.alice)
        self.run_job()
        day = self.days(self.alice).filtered(lambda d: d.date == local_date(10))
        self.assertTrue(day.insights_included)
        self.assertAlmostEqual(day.meeting_hours, 1.0, places=2)

    def test_indicators_turned_on_later_reach_the_earlier_days(self):
        """The notice of the indicators promises them over the earlier days, even taken in before."""
        self.meeting(local(10, 14))
        consent = self.take_in(self.alice)
        self.run_job()
        day = self.days(self.alice).filtered(lambda d: d.date == local_date(10))
        self.assertFalse(day.insights_included)
        with patch.object(type(self.env["ir.cron"]), "_trigger") as trigger:
            self.activate(self.alice, "insights")
        self.assertTrue(trigger.called, "The job starts again at once.")
        self.assertFalse(consent.computed_from, "The earlier days are taken in again, from the start.")
        self.run_job()
        day = self.days(self.alice).filtered(lambda d: d.date == local_date(10))
        self.assertTrue(day.insights_included)
        self.assertAlmostEqual(day.meeting_hours, 1.0, places=2)

    def test_indicators_skip_the_days_between_the_hours_and_them(self):
        """Turned on after the hours: the earlier days taken in get them, the days in between do not."""
        self.meeting(local(10, 14))
        self.meeting(local(1, 14))
        self.take_in(self.alice)
        self.activate(self.alice, "insights")             # today, two days after the hours
        self.run_job()
        self.Day._bf_compute(self.alice, [local_date(n) for n in range(3)])  # the hourly job
        days = {d.date: d for d in self.days(self.alice)}
        self.assertTrue(days[local_date(10)].insights_included, "An earlier day taken in.")
        self.assertAlmostEqual(days[local_date(10)].meeting_hours, 1.0, places=2)
        between = days[local_date(1)]
        self.assertAlmostEqual(between.hours, 1.0, places=2, msg="Its hours are computed.")
        self.assertFalse(between.insights_included, "Between the hours and the indicators: none, as without "
                                                    "earlier days.")
        self.assertFalse(between.meeting_hours)
        self.assertFalse(days[local_date(2)].insights_included, "Nor the day of the activation of the hours.")
        self.assertTrue(days[local_date(0)].insights_included)

    def test_a_span_that_adds_nothing_is_refused(self):
        """Hours on for 40 days: « the last 30 days » would read nothing more (the screen greys it out)."""
        self.Consent.sudo().search([("user_id", "=", self.alice.id), ("scope", "=", "hours")]).write(
            {"granted_at": local(40)})
        with self.assertRaises(UserError):
            self.take_in(self.alice, "30")
        with self.assertRaises(UserError):
            self.Consent.with_user(self.alice).create({"scope": "history", "history_span": "30"})
        self.assertFalse(self.Consent.sudo().search([("user_id", "=", self.alice.id), ("scope", "=", "history")]))
        consent = self.take_in(self.alice, "all")         # 45 days back: further than the hours
        self.assertTrue(consent.read_from < local(40))

    def test_turning_it_off_destroys_the_earlier_days(self):
        self.meeting(local(10, 14))
        self.meeting(self.since - timedelta(hours=2))     # 7:00 on the day of the activation
        self.meeting(local(1, 14))                        # after the activation
        self.take_in(self.alice)
        self.run_job()
        self.Day._bf_compute(self.alice, [local_date(1)])
        activation_day = self.days(self.alice).filtered(lambda d: d.date == local_date(2))
        self.assertAlmostEqual(activation_day.hours, 1.0, places=2)
        with acting_as(self.alice.id):
            self.withdraw(self.alice, "history")
        dates = self.days(self.alice).mapped("date")
        self.assertNotIn(local_date(10), dates)
        self.assertIn(local_date(1), dates, "The days since the activation stay.")
        activation_day = self.days(self.alice).filtered(lambda d: d.date == local_date(2))
        self.assertFalse(activation_day.hours, "The day of the activation no longer reads before it.")
        self.Day._bf_compute(self.alice, [local_date(10)])
        self.assertNotIn(local_date(10), self.days(self.alice).mapped("date"))

    def test_turning_it_off_redoes_the_next_day_too(self):
        """A stretch from before the activation that ran up to midnight reached the next day."""
        self.env["ir.config_parameter"].sudo().set_param("bf_work_hours.gap_minutes", 60)
        self.Consent.sudo().search([("user_id", "=", self.alice.id), ("scope", "=", "hours")]).write(
            {"granted_at": local(2, 23, 30)})
        self.meeting(local(2, 22, 30), hours=50 / 60)    # 22:30 to 23:20, before the activation
        self.meeting(local(1, 0, 10), hours=0.5)         # 0:10 to 0:40, the next day
        self.take_in(self.alice)
        self.run_job()
        self.Day._bf_compute(self.alice, [local_date(1)])  # the hourly job
        next_day = self.days(self.alice).filtered(lambda d: d.date == local_date(1))
        self.assertAlmostEqual(next_day.hours, 40 / 60, places=2, msg="Joined to the evening before.")
        self.withdraw(self.alice, "history")
        next_day = self.days(self.alice).filtered(lambda d: d.date == local_date(1))
        self.assertAlmostEqual(next_day.hours, 0.5, places=2)

    def test_turning_it_off_survives_a_failed_recompute(self):
        """The withdrawal is not blocked by the recompute, and keeps nothing read before the activation."""
        self.meeting(local(10, 14))
        self.meeting(self.since - timedelta(hours=2))     # 7:00 on the day of the activation
        consent = self.take_in(self.alice)
        self.run_job()
        self.assertIn(local_date(2), self.days(self.alice).mapped("date"))
        with patch.object(type(self.Day), "_bf_compute", side_effect=ValueError("boom")), \
                mute_logger("odoo.addons.bf_work_hours.models.work_day"):
            self.withdraw(self.alice, "history")
        self.assertTrue(consent.withdrawn_at)
        dates = self.days(self.alice).mapped("date")
        self.assertNotIn(local_date(10), dates)
        self.assertNotIn(local_date(2), dates, "Not redone: the day of the activation goes, with what it read before.")

    def test_turning_the_hours_off_ends_the_earlier_days(self):
        consent = self.take_in(self.alice)
        self.withdraw(self.alice, "hours")
        self.assertTrue(consent.withdrawn_at)
        self.assertFalse(self.days(self.alice))

    def test_the_purge_finishes_a_withdrawal_cut_short(self):
        consent = self.take_in(self.alice)
        self.meeting(local(10, 14))
        self.run_job()
        self.assertIn(local_date(10), self.days(self.alice).mapped("date"))
        # Withdrawn without what a withdrawal destroys (a crash half way).
        self.env.flush_all()
        self.env.cr.execute("UPDATE bf_work_consent SET withdrawn_at = now() AT TIME ZONE 'UTC' WHERE id = %s",
                            [consent.id])
        self.env.invalidate_all()
        with patch.object(self.env.cr, "commit"):
            self.Day._bf_purge()
        self.assertNotIn(local_date(10), self.days(self.alice).mapped("date"))

    def test_pushed_traces_follow_the_start_of_reading(self):
        # Turned on a minute ago: a trace of an hour ago predates it.
        self.activate(self.bob)
        an_hour_ago = fields.Datetime.to_string(fields.Datetime.now() - timedelta(hours=1))
        with self.assertRaises(UserError):
            self.Signal.with_user(self.bob).bf_work_push([{"source": "terminal", "start": an_hour_ago}])
        Signal = self.Signal.with_user(self.alice)
        before = fields.Datetime.to_string(self.since - timedelta(hours=3))
        # 🔴 Before the activation, not collected at all (18.0.1.0.2 kept 48 hours of them).
        with self.assertRaises(UserError):
            Signal.bf_work_push([{"source": "terminal", "start": before}])
        self.assertEqual(Signal.bf_work_push_window()["enabled"], True)
        self.take_in(self.alice)
        old = local(10, 15)
        self.assertEqual(Signal.bf_work_push([{"source": "terminal", "start": fields.Datetime.to_string(old),
                                               "stop": fields.Datetime.to_string(old + timedelta(minutes=30))}]), 1)
        day = self.days(self.alice).filtered(lambda d: d.date == local_date(10))
        self.assertAlmostEqual(day.source_hours.get("terminal", 0), 0.5, places=2,
                               msg="An earlier day the hourly job no longer redoes is redone at the push.")
        window = Signal.bf_work_push_window()
        self.assertLess(fields.Datetime.to_datetime(window["earliest"]), self.since - timedelta(days=20))
        # Never beyond the retention of the raw traces, whatever the span.
        self.env["ir.config_parameter"].sudo().set_param("bf_work_hours.signal_retention_days", 5)
        with self.assertRaises(UserError):
            Signal.bf_work_push([{"source": "terminal", "start": fields.Datetime.to_string(local(10, 16))}])

    def test_the_push_window_is_the_callers(self):
        self.assertEqual(self.Signal.with_user(self.bob).bf_work_push_window(), {"enabled": False, "earliest": False})
        with self.assertRaises(UserError):
            self.Signal.sudo().bf_work_push_window()

    def test_the_recompute_button_says_what_it_did(self):
        Day = self.Day.with_user(self.alice).with_context(lang="en_US")
        action = Day.action_bf_recompute()
        self.assertEqual(action["tag"], "display_notification")
        message = action["params"]["message"]
        self.assertIn("Nothing is read before you turned it on", message)
        self.take_in(self.alice)
        message = Day.action_bf_recompute()["params"]["message"]
        self.assertIn("being taken in", message)
        self.run_job()
        message = Day.action_bf_recompute()["params"]["message"]
        self.assertIn("are taken in from", message)

    def test_my_choices_shows_the_earlier_days(self):
        page = self.env["bf.work.choices"].with_user(self.alice).with_context(lang="en_US").create({})
        self.assertFalse(page.history_enabled)
        action = page.action_activate_history()
        self.assertEqual(action["params"], {"scope": "history", "mode": "activate"})
        self.take_in(self.alice)
        page.invalidate_recordset()
        self.assertTrue(page.history_enabled)
        self.assertIn("Starting", page.history_status, "Nothing taken in yet.")
        # One slice of 5 days, then out of time.
        with patch("odoo.addons.bf_work_hours.models.work_day.HISTORY_SLICE_DAYS", 5), \
                patch("odoo.addons.bf_work_hours.models.work_day._monotonic",
                      side_effect=itertools.chain([0, 0], itertools.repeat(100))):
            self.run_job()
        page.invalidate_recordset()
        self.assertIn("In progress", page.history_status)
        self.run_job()
        page.invalidate_recordset()
        self.assertTrue(page.history_status.startswith("From"))
        page.action_withdraw_history()
        page.invalidate_recordset()
        self.assertFalse(page.history_enabled)

    def test_the_screen_shows_the_readers_state_only(self):
        Wizard = self.env["bf.work.activate.wizard"]
        with acting_as(self.alice.id):
            data = Wizard.with_user(self.alice).bf_screen_data("hours")
        self.assertTrue(data["can_act"])
        self.assertTrue(data["state"]["hours"])
        self.assertIn("o_bf_work_notice_hours", data["notice"])
        spans = {o["span"]: o for o in data["history"]}
        self.assertEqual(set(spans), {"none", "30", "all"})
        self.assertIn("o_bf_work_notice_history", spans["30"]["notice"])
        # Hours on for 40 days: « the last 30 days » adds nothing.
        self.Consent.sudo().search([("user_id", "=", self.alice.id), ("scope", "=", "hours")]).write(
            {"granted_at": local(40)})
        with acting_as(self.alice.id):
            spans = {o["span"]: o for o in Wizard.with_user(self.alice).bf_screen_data("history")["history"]}
        self.assertNotIn("none", spans)
        self.assertFalse(spans["30"]["available"])
        self.assertTrue(spans["all"]["available"])
        # Someone impersonating the person learns nothing of their state, and cannot act.
        with acting_as(self.alice.id, bf_impersonate=99):
            data = Wizard.with_user(self.alice).bf_screen_data("hours")
        self.assertFalse(data["can_act"])
        self.assertFalse(any(data["state"].values()))
        with self.assertRaises(UserError):
            Wizard.with_user(self.alice).bf_screen_data("everything")

    def test_the_hours_and_the_earlier_days_on_one_screen(self):
        """The first screen turns on both: two activations, each with its notice."""
        wizard = self.env["bf.work.activate.wizard"].with_user(self.bob).create(
            {"scope": "hours", "understood": True, "history_span": "30"})
        self.assertIn("o_bf_work_notice_history", wizard.notice, "The screen shows both notices.")
        wizard.action_confirm()
        consents = self.Consent.sudo().search([("user_id", "=", self.bob.id)])
        self.assertEqual(sorted(consents.mapped("scope")), ["history", "hours"])
        hours = consents.filtered(lambda c: c.scope == "hours")
        self.assertNotIn("o_bf_work_notice_history", hours.notice_text)
        self.assertIn("o_bf_work_notice_history", consents.filtered(lambda c: c.scope == "history").notice_text)

    def test_the_notice_says_how_far_back_each_source_reaches(self):
        """What the earlier days really read: the raw traces kept, the calendar as stamped at installation."""
        self.env["ir.config_parameter"].sudo().set_param("bf_work_hours.signal_retention_days", 17)
        notice = self.env["bf.work.activate.wizard"].with_user(self.alice).with_context(
            lang="en_US")._bf_history_notice("all")
        self.assertIn("no further than the raw traces are kept: 17 days", notice)
        self.assertIn("before this feature was installed", notice)
        self.assertIn("A system administrator", notice, "Who sees it: the same as the notice of the hours.")
