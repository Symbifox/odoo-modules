from datetime import date, datetime, timedelta
from unittest.mock import patch

from odoo import SUPERUSER_ID

from odoo.tests import tagged
from odoo.tools import mute_logger

from .common import WorkHoursCase, acting_as

DAY = date(2026, 10, 7)


def mtl(h, m=0, d=7):
    """Naive UTC for a Montreal wall-clock time (UTC-4 in October)."""
    return datetime(2026, 10, d, h, m) + timedelta(hours=4)


@tagged("post_install", "-at_install")
class TestDays(WorkHoursCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["ir.config_parameter"].sudo().set_param("bf_work_hours.gap_minutes", 15)

    def compute(self, user, *days):
        return self.Day._bf_compute(user, list(days or [DAY]))

    def meeting(self, start, hours, partners, state="accepted", show_as="busy"):
        event = self.env["calendar.event"].sudo().create({
            "name": "WH meeting", "start": start, "stop": start + timedelta(hours=hours),
            "partner_ids": [(6, 0, [p.id for p in partners])], "show_as": show_as,
        })
        event.attendee_ids.write({"state": state})
        return event

    def test_nothing_without_activation(self):
        self.signal(self.alice, mtl(10), 60)
        self.assertFalse(self.compute(self.alice))

    def test_days_before_the_activation_are_never_read(self):
        self.activate(self.alice, granted_at=mtl(12))
        self.signal(self.alice, mtl(10), 60)
        self.assertFalse(self.compute(self.alice, DAY - timedelta(days=1)))
        day = self.compute(self.alice, DAY - timedelta(days=1), DAY)
        self.assertEqual(day.date, DAY, "The day of the activation counts, the day before does not.")

    def test_hours_from_presence_and_meetings(self):
        self.activate(self.alice, granted_at=mtl(0, 1))
        self.signal(self.alice, mtl(9), 60)            # 9:00-10:00 in the browser
        self.meeting(mtl(9, 30), 1, [self.alice.partner_id, self.bob.partner_id])  # 9:30-10:30
        day = self.compute(self.alice)
        self.assertAlmostEqual(day.hours, 1.5, places=2)
        self.assertEqual(day.source_hours, {"calendar": 1.0, "presence": 1.0})
        self.assertFalse(day.insights_included)
        self.assertFalse(day.meeting_hours, "Indicators stay empty until the person turns them on.")

    def test_meeting_rules(self):
        self.activate(self.alice, granted_at=mtl(0, 1))
        both = [self.alice.partner_id, self.bob.partner_id]
        self.meeting(mtl(9), 1, [self.alice.partner_id])             # alone: a block, not a meeting
        self.meeting(mtl(11), 1, both, state="declined")              # declined
        self.meeting(mtl(13), 1, both, show_as="free")                # free
        self.meeting(mtl(0), 9, both)                                 # 9 hours: a block
        self.assertFalse(self.compute(self.alice).hours)
        self.meeting(mtl(15), 1, both)
        self.assertAlmostEqual(self.compute(self.alice).hours, 1.0, places=2)

    def test_indicators_once_turned_on(self):
        self.activate(self.alice, granted_at=mtl(0, 1))
        self.activate(self.alice, "insights", granted_at=mtl(0, 2))
        self.signal(self.alice, mtl(8), 60)
        self.meeting(mtl(9), 1, [self.alice.partner_id, self.bob.partner_id])
        day = self.compute(self.alice)
        self.assertTrue(day.insights_included)
        self.assertAlmostEqual(day.meeting_hours, 1.0, places=2)
        self.assertAlmostEqual(day.span_hours, 2.0, places=2)
        self.assertEqual(day.first_activity, mtl(8))

    def test_turning_indicators_off_clears_them(self):
        self.activate(self.alice, granted_at=mtl(0, 1))
        self.activate(self.alice, "insights", granted_at=mtl(0, 2))
        self.signal(self.alice, mtl(8), 60)
        day = self.compute(self.alice)
        self.assertTrue(day.span_hours)
        self.alice.with_user(self.alice).with_context(bf_work_scope="insights").action_bf_work_withdraw()
        self.assertFalse(day.span_hours)
        self.assertFalse(day.first_activity)
        self.assertAlmostEqual(day.hours, 1.0, places=2, msg="The hours stay: only indicators were turned off.")

    def test_recompute_updates_the_same_line(self):
        self.activate(self.alice, granted_at=mtl(0, 1))
        self.signal(self.alice, mtl(9), 30)
        first = self.compute(self.alice)
        self.signal(self.alice, mtl(14), 30)
        second = self.compute(self.alice)
        self.assertEqual(first, second)
        self.assertAlmostEqual(second.hours, 1.0, places=2)

    def test_purges(self):
        self.activate(self.alice, granted_at=mtl(0, 1))
        self.signal(self.alice, datetime(2020, 1, 1), 10)
        self.Day.sudo().create({"user_id": self.alice.id, "date": date(2020, 1, 1), "hours": 1})
        self.Day._bf_purge()
        self.Signal._bf_purge()
        self.assertFalse(self.Day.sudo().search([("user_id", "=", self.alice.id), ("date", "=", date(2020, 1, 1))]))
        self.assertFalse(self.Signal.sudo().search([("user_id", "=", self.alice.id), ("date_start", "<", mtl(0))]))

    def test_terminal_traces_pushed_by_the_person(self):
        self.activate(self.alice, granted_at=mtl(0, 1))
        Signal = self.Signal.with_user(self.alice)
        recent = datetime.now().replace(microsecond=0) - timedelta(hours=1)
        rows = [{"source": "terminal", "start": str(recent)},
                {"source": "terminal", "start": str(recent + timedelta(minutes=10))}]
        self.assertEqual(Signal.bf_work_push(rows), 2)
        self.assertEqual(Signal.bf_work_push(rows), 0, "Known traces are skipped.")
        self.assertEqual(len(Signal.search([("source", "=", "terminal")])), 2)

    def test_pushing_needs_the_activation_and_a_known_source(self):
        from odoo.exceptions import AccessError, UserError
        with self.assertRaises(AccessError):
            self.Signal.with_user(self.bob).bf_work_push([{"source": "terminal", "start": "2026-10-07 14:00:00"}])
        self.activate(self.bob, granted_at=mtl(0, 1))
        recent = str(datetime.now().replace(microsecond=0) - timedelta(hours=1))
        with self.assertRaises(UserError):
            self.Signal.with_user(self.bob).bf_work_push([{"source": "presence", "start": recent}])
        with self.assertRaises(UserError):
            self.Signal.with_user(self.bob).bf_work_push([{"source": "terminal", "start": "2099-01-01 00:00:00"}])
        with self.assertRaises(UserError):  # 🟡 a year of history is not a local tool's report
            self.Signal.with_user(self.bob).bf_work_push(
                [{"source": "terminal", "start": str(datetime.now().replace(microsecond=0) - timedelta(days=3))}])


@tagged("post_install", "-at_install")
class TestDaysGuards(WorkHoursCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["ir.config_parameter"].sudo().set_param("bf_work_hours.gap_minutes", 15)

    def test_indicators_only_from_their_own_activation(self):
        """🟠 Hours on since the 5th, indicators since the 7th: no indicator on the 5th or 6th."""
        self.activate(self.alice, granted_at=mtl(0, 1, d=5))
        insights = self.activate(self.alice, "insights")
        insights.sudo().write({"granted_at": mtl(0, 1, d=7)})
        for d in (5, 6, 7):
            self.signal(self.alice, mtl(9, d=d), 60)
        days = {r.date.day: r for r in self.Day._bf_compute(self.alice, [date(2026, 10, d) for d in (5, 6, 7)])}
        self.assertFalse(days[5].insights_included or days[6].insights_included)
        self.assertFalse(days[5].span_hours or days[6].first_activity)
        self.assertTrue(days[7].insights_included and days[7].span_hours)

    def test_no_minute_before_the_activation(self):
        """🟡 A stretch that started before the activation counts from the activation only."""
        self.activate(self.alice, granted_at=mtl(10))
        self.signal(self.alice, mtl(9), 120)  # 9:00-11:00
        self.assertAlmostEqual(self.Day._bf_compute(self.alice, [DAY]).hours, 1.0, places=2)

    def test_a_meeting_accepted_by_someone_else_does_not_count(self):
        """🟠 Bob can edit the event: he cannot accept it for Alice."""
        self.activate(self.alice, granted_at=mtl(0, 1))
        self.bob.groups_id = [(4, self.env.ref("base.group_user").id)]
        event = self.env["calendar.event"].with_user(self.bob).create({
            "name": "WH forged", "start": mtl(8), "stop": mtl(15),
            "partner_ids": [(6, 0, [self.alice.partner_id.id, self.bob.partner_id.id])],
        })
        event.attendee_ids.filtered(lambda a: a.partner_id == self.alice.partner_id).with_user(self.bob).write(
            {"state": "accepted"})
        self.assertFalse(self.Day._bf_compute(self.alice, [DAY]).hours)

    def test_the_job_goes_on_when_one_person_fails(self):
        """🟠 One failure neither stops the others nor the purge."""
        self.activate(self.alice, granted_at=mtl(0, 1))
        self.activate(self.bob, granted_at=mtl(0, 1))
        self.signal(self.alice, datetime(2020, 1, 1), 10)
        original = type(self.Day)._bf_compute

        def boom(model, user, dates):
            if user == self.alice:
                raise ValueError("boom")
            return original(model, user, dates)

        with patch.object(type(self.Day), "_bf_compute", boom), patch.object(self.env.cr, "commit"), \
                mute_logger("odoo.addons.bf_work_hours.models.work_day"):
            self.Day._cron_bf_compute()
        self.assertTrue(self.Day.sudo().search([("user_id", "=", self.bob.id)]))
        self.assertFalse(self.Signal.sudo().search([("user_id", "=", self.alice.id), ("date_start", "<", mtl(0))]))

    def test_a_pushed_trace_is_short(self):
        """🟡 « From Monday to now » is not a trace."""
        from odoo.exceptions import UserError
        self.activate(self.alice, granted_at=mtl(0, 1))
        start = datetime.now().replace(microsecond=0) - timedelta(hours=20)
        with self.assertRaises(UserError):
            self.Signal.with_user(self.alice).bf_work_push(
                [{"source": "terminal", "start": str(start), "stop": str(start + timedelta(hours=16))}])


@tagged("post_install", "-at_install")
class TestCalendarAnswers(WorkHoursCase):
    """Who answered for the person, and for which hours."""

    def setUp(self):
        super().setUp()
        self.activate(self.alice, granted_at=mtl(0, 1))

    def event(self, author, start, hours, organizer=None):
        return self.env["calendar.event"].with_user(author).create({
            "name": "WH answer", "start": start, "stop": start + timedelta(hours=hours),
            "user_id": (organizer or author).id,
            "partner_ids": [(6, 0, [self.alice.partner_id.id, self.bob.partner_id.id])],
        })

    def line(self, event, user):
        return event.attendee_ids.filtered(lambda a: a.partner_id == user.partner_id)

    def hours(self):
        return self.Day._bf_compute(self.alice, [DAY]).hours

    def test_accepted_by_the_person_counts(self):
        event = self.event(self.bob, mtl(9), 1)
        with acting_as(self.alice.id):
            self.line(event, self.alice).with_user(self.alice).do_accept()
        self.assertEqual(self.line(event, self.alice).bf_work_state_by, "self")
        self.assertAlmostEqual(self.hours(), 1.0, places=2)

    def test_naming_the_person_organizer_does_not_help(self):
        """🟠 Bob makes Alice the organizer, then accepts for her: not hers."""
        event = self.event(self.bob, mtl(8), 7.9, organizer=self.alice)
        self.line(event, self.alice).with_user(self.bob).write({"state": "accepted"})
        self.assertEqual(self.line(event, self.alice).bf_work_state_by, "other")
        self.assertFalse(self.hours())

    def test_moved_after_the_answer_counts_the_accepted_hours_only(self):
        """🟠 Alice accepts one hour; Bob stretches the meeting to eight: one hour counts."""
        event = self.event(self.bob, mtl(9), 1)
        with acting_as(self.alice.id):
            self.line(event, self.alice).with_user(self.alice).do_accept()
        event.with_user(self.bob).write({"stop": mtl(16, 59)})
        self.assertAlmostEqual(self.hours(), 1.0, places=2)

    def test_a_recomputed_line_keeps_its_stamp(self):
        """🟠 A forged answer is not laundered when Odoo later rewrites the line."""
        event = self.event(self.bob, mtl(9), 1)
        line = self.line(event, self.alice)
        line.with_user(self.bob).write({"state": "accepted"})
        line.sudo().write({"common_name": "WH renamed"})
        self.assertEqual(line.bf_work_state_by, "other")
        self.assertFalse(self.hours())

    def test_the_invitation_link_counts(self):
        """Accepted from the link in the invitation (Odoo answers, as superuser)."""
        event = self.event(self.bob, mtl(9), 1)
        self.line(event, self.alice).sudo().do_accept()
        self.assertEqual(self.line(event, self.alice).bf_work_state_by, "odoo")
        self.assertAlmostEqual(self.hours(), 1.0, places=2)

    def test_the_stamp_is_not_writable(self):
        """🟠 Bob accepts for Alice, then writes the stamp himself: dropped."""
        event = self.event(self.bob, mtl(9), 1)
        line = self.line(event, self.alice)
        line.with_user(self.bob).write({"state": "accepted"})
        line.with_user(self.bob).write({"bf_work_state_by": "self", "bf_work_state_start": mtl(9),
                                        "bf_work_state_stop": mtl(10)})
        self.assertEqual(line.bf_work_state_by, "other")
        self.assertFalse(self.hours())

    def test_the_invitation_link_opened_in_someone_elses_session(self):
        """🟠 Bob reads Alice's token and opens the link: Odoo answers as superuser, in Bob's session."""
        event = self.event(self.bob, mtl(9), 1)
        with acting_as(self.bob.id, "/calendar/meeting/accept"):
            self.line(event, self.alice).sudo().do_accept()
        self.assertEqual(self.line(event, self.alice).bf_work_state_by, "other")
        self.assertFalse(self.hours())

    def test_naming_someone_organizer_of_an_odoo_event(self):
        """🔴 an event Odoo made, Bob names Alice organizer: not hers."""
        event = self.env["calendar.event"].sudo().create({
            "name": "WH synced", "start": mtl(9), "stop": mtl(16, 50),
            "partner_ids": [(6, 0, [self.alice.partner_id.id, self.bob.partner_id.id])],
        })
        event.with_user(self.bob).write({"user_id": self.alice.id})
        self.assertFalse(self.hours())

    def test_handing_ones_own_answer_to_someone_else(self):
        """🔴 Bob accepts his own line, then moves it to Alice."""
        other = self.env["res.partner"].create({"name": "WH contact"})
        event = self.env["calendar.event"].with_user(self.bob).create({
            "name": "WH handed", "start": mtl(9), "stop": mtl(16, 50), "user_id": self.bob.id,
            "partner_ids": [(6, 0, [self.bob.partner_id.id, other.id])],
        })
        with acting_as(self.bob.id):
            self.line(event, self.bob).with_user(self.bob).do_accept()
        self.line(event, self.bob).with_user(self.bob).write({"partner_id": self.alice.partner_id.id})
        self.assertEqual(event.attendee_ids.filtered(lambda a: a.partner_id == self.alice.partner_id
                                                     ).bf_work_state_by, "other")
        self.assertFalse(self.hours())

    def test_her_own_meeting_stretched_by_someone_else(self):
        """🔴 Alice's own one-hour meeting, stretched by Bob: never the stretched hours.

        Odoo asks the organizer to answer again when someone else moves her
        meeting (her line goes back to "needs action"): until she does, it counts
        for nothing; and an answer Bob's change wrote is not hers anyway.
        """
        with acting_as(self.alice.id):
            event = self.event(self.alice, mtl(9), 1)
            self.line(event, self.alice).with_user(self.alice).do_accept()
        self.assertAlmostEqual(self.hours(), 1.0, places=2)
        event.with_user(self.bob).write({"stop": mtl(16, 50)})
        self.assertLessEqual(self.hours(), 1.0)

    def test_she_moves_her_own_meeting(self):
        """🟠 S2b: Alice moves her meeting to the afternoon: it counts at its new hours."""
        with acting_as(self.alice.id):
            event = self.event(self.alice, mtl(9), 1)
            self.line(event, self.alice).with_user(self.alice).do_accept()
            event.with_user(self.alice).write({"start": mtl(14), "stop": mtl(15)})
        line = self.line(event, self.alice)
        self.assertEqual((line.bf_work_state_start, line.bf_work_state_stop), (mtl(14), mtl(15)))
        self.assertAlmostEqual(self.hours(), 1.0, places=2)

    def test_an_answer_from_before_the_installation(self):
        """🟠 an answer given before the module is stamped at installation, then cannot be stretched."""
        event = self.event(self.bob, mtl(9), 1)
        line = self.line(event, self.alice)
        with acting_as(self.alice.id):
            line.with_user(self.alice).do_accept()
        self.env.flush_all()
        self.env.cr.execute("UPDATE calendar_attendee SET bf_work_state_by = NULL, bf_work_state_start = NULL, "
                            "bf_work_state_stop = NULL WHERE id = %s", [line.id])
        self.env.invalidate_all()
        self.assertFalse(self.hours(), "Unstamped: counts for nothing.")
        self.env["calendar.attendee"]._bf_work_stamp_existing()
        self.assertEqual(line.bf_work_state_by, "self")
        event.with_user(self.bob).write({"stop": mtl(16, 50)})
        self.assertAlmostEqual(self.hours(), 1.0, places=2)


    def test_an_answer_odoo_gave_before_the_installation(self):
        """A synchronization accepted for her before the module: stamped « odoo » at installation, it counts."""
        event = self.event(self.bob, mtl(9), 1)
        line = self.line(event, self.alice)
        line.sudo().write({"state": "accepted"})
        self.env.flush_all()
        self.env.cr.execute("UPDATE calendar_attendee SET bf_work_state_by = NULL, bf_work_state_start = NULL, "
                            "bf_work_state_stop = NULL, write_uid = %s WHERE id = %s", [SUPERUSER_ID, line.id])
        self.env.invalidate_all()
        self.env["calendar.attendee"]._bf_work_stamp_existing()
        self.assertEqual(line.bf_work_state_by, "odoo")
        self.assertAlmostEqual(self.hours(), 1.0, places=2)

    def test_the_invitation_link_in_her_own_session(self):
        """🟠 A GET a tag in a message can fire in her session: not proof of a hand."""
        event = self.event(self.bob, mtl(9), 1)
        with acting_as(self.alice.id, "/calendar/meeting/accept"):
            self.line(event, self.alice).sudo().do_accept()
        self.assertEqual(self.line(event, self.alice).bf_work_state_by, "other")
        self.assertFalse(self.hours())

    def test_an_archived_or_cancelled_occurrence_is_not_a_meeting(self):
        """🔴 A rewritten series leaves archived occurrences behind; a cancelled meeting stays active."""
        with acting_as(self.alice.id):
            archived = self.event(self.alice, mtl(9), 1)
            self.line(archived, self.alice).with_user(self.alice).do_accept()
            cancelled = self.event(self.alice, mtl(13), 1)
            self.line(cancelled, self.alice).with_user(self.alice).do_accept()
        self.assertAlmostEqual(self.hours(), 2.0, places=2)
        archived.sudo().active = False
        if "bf_event_status" in cancelled._fields:
            cancelled.sudo().bf_event_status = "cancelled"
            self.assertFalse(self.hours())
        else:
            self.assertAlmostEqual(self.hours(), 1.0, places=2)
