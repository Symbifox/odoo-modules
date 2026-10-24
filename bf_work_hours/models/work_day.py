import logging
import time as clock
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from odoo import _, api, fields, models
from odoo.exceptions import AccessError
from odoo.tools.misc import format_date

from . import engine
from .work_collector import LIVE_SOURCES

# Figures that describe HOW one works rather than how long: computed and kept
# only for people who also activated "My indicators".
INSIGHT_FIELDS = (
    "first_activity", "last_activity", "span_hours", "longest_session_hours",
    "meeting_hours", "back_to_back_meetings", "outside_hours", "schedule_known",
)
RECOMPUTE_DAYS = 7
# Taking in the earlier days: a slice of days per transaction, and a time budget per
# run of the job, which runs again at once while days remain (ir.cron progress).
HISTORY_SLICE_DAYS = 31
HISTORY_RUN_SECONDS = 50


def _monotonic():
    """The job's clock, on its own so that a test can stop the job after a slice."""
    return clock.monotonic()

_logger = logging.getLogger(__name__)


class WorkDay(models.Model):
    """One person's reconstructed working time for one local day.

    Readable by the person, and by the people they chose to share it with
    (``bf.work.share``). Nobody else: no manager, no administrator. There is
    no score here and nothing triggers anything: these are figures for the
    person, with their uncertainty band.
    """

    _name = "bf.work.day"
    _description = "Working time: one day"
    _order = "date desc, user_id"
    _rec_name = "date"
    # bf_impersonate hides these from whoever impersonates the person.
    _gen_scope = "bf_work_hours"

    user_id = fields.Many2one("res.users", string="Person", required=True, readonly=True, index=True,
                              ondelete="cascade")
    date = fields.Date(required=True, readonly=True, index=True)
    gap_minutes = fields.Integer(string="Idle gap (minutes)", readonly=True,
                                 help="Two traces closer than this belong to the same stretch of work.")
    hours = fields.Float(string="Reconstructed hours", readonly=True, digits=(16, 2))
    hours_low = fields.Float(string="Low bound", readonly=True, digits=(16, 2),
                             help="The same day with a 5-minute idle gap.")
    hours_high = fields.Float(string="High bound", readonly=True, digits=(16, 2),
                              help="The same day with a 30-minute idle gap.")
    session_count = fields.Integer(string="Stretches", readonly=True)
    source_hours = fields.Json(readonly=True,
                               help="Hours each source would give alone, to show what the figure rests on.")
    source_summary = fields.Text(string="What it rests on", compute="_compute_source_summary")
    insights_included = fields.Boolean(readonly=True)
    first_activity = fields.Datetime(string="First trace", readonly=True)
    last_activity = fields.Datetime(string="Last trace", readonly=True)
    span_hours = fields.Float(string="Span (first to last trace)", readonly=True, digits=(16, 2))
    longest_session_hours = fields.Float(string="Longest stretch", readonly=True, digits=(16, 2))
    meeting_hours = fields.Float(string="In meetings", readonly=True, digits=(16, 2))
    back_to_back_meetings = fields.Integer(string="Meetings without a 10-minute break", readonly=True)
    outside_hours = fields.Float(string="Outside working hours", readonly=True, digits=(16, 2),
                                 help="Reconstructed time outside the working schedule of the person.")
    schedule_known = fields.Boolean(readonly=True,
                                    help="False when the person has no working schedule: "
                                         "the time outside working hours is then not computed.")

    _sql_constraints = [
        ("user_date_unique", "unique(user_id, date)", "One line per person and per day."),
    ]

    @api.depends("source_hours")
    def _compute_source_summary(self):
        labels = self._bf_source_labels()
        for day in self:
            parts = [f"{labels.get(k, k)} {self._bf_duration(v)}" for k, v in (day.source_hours or {}).items() if v]
            day.source_summary = ", ".join(parts)

    @staticmethod
    def _bf_duration(hours):
        """« 2 min », « 1 h 05 »: the same in French and in English, no decimal separator."""
        minutes = round(hours * 60)
        return f"{minutes} min" if minutes < 60 else f"{minutes // 60} h {minutes % 60:02d}"

    @api.model
    def _bf_source_labels(self):
        return {key: label for key, label, _text in self.env["bf.work.collector"]._bf_source_descriptions()}

    # ------------------------------------------------------------------
    # Computation
    # ------------------------------------------------------------------

    @api.model
    def _bf_gap_minutes(self):
        value = self.env["ir.config_parameter"].sudo().get_param("bf_work_hours.gap_minutes", engine.DEFAULT_GAP)
        try:
            return min(max(int(value), 1), 120)
        except (TypeError, ValueError):
            return engine.DEFAULT_GAP

    @api.model
    def _bf_compute(self, user, dates):
        """(Re)compute the days of one person.

        Nothing before the activation of the hours is read, unless the person chose
        to take in their earlier days: then nothing before the moment they chose.
        """
        Consent = self.env["bf.work.consent"].sudo()
        read_from = Consent._bf_read_from(user)
        if not read_from:
            return self.browse()
        since = Consent._bf_since(user, "hours")
        tz = self._bf_tz(user)
        dates = sorted(d for d in set(dates) if d >= self._bf_local_date(read_from, tz))
        if not dates:
            return self.browse()
        insights_since = Consent._bf_since(user, "insights")
        insights_from = self._bf_local_date(insights_since, tz) if insights_since else None
        # The indicators follow the hours back over the earlier days taken in: the days
        # BEFORE the activation of the hours. Between it and the activation of the
        # indicators, none, as without earlier days.
        history_from = Consent._bf_history(user).read_from
        earlier = (self._bf_local_date(history_from, tz), self._bf_local_date(since, tz)) \
            if insights_from and history_from else None
        gap = self._bf_gap_minutes()
        margin = timedelta(minutes=max(gap, engine.HIGH_GAP) + 60)
        start = max(engine.local_day_bounds(dates[0], tz)[0] - margin, read_from)
        stop = engine.local_day_bounds(dates[-1], tz)[1] + margin
        collector = self.env["bf.work.collector"].sudo()
        # Nothing before the start of reading, not even the margin of the first day. The
        # sources captured live start at the activation of the hours, earlier days or not
        # (a timer started before it and paused after: only the part after counts).
        collected = collector._bf_collect(user, start, stop)
        items = (engine.not_before([i for i in collected if i.source in LIVE_SOURCES], since)
                 + engine.not_before([i for i in collected if i.source not in LIVE_SOURCES], read_from))
        point_minutes = collector._bf_point_minutes()
        insight_dates = [d for d in dates if insights_from and (
            d >= insights_from or (earlier and earlier[0] <= d < earlier[1]))]
        schedules = self._bf_schedules(user, insight_dates, tz) if insight_dates else {}
        days = self.browse()
        for day in dates:
            insights = day in insight_dates
            figures = engine.day_figures(items, day, tz, gap, point_minutes, schedules.get(day), read_from)
            vals = {
                "gap_minutes": gap,
                "hours": figures["hours"],
                "hours_low": figures["hours_low"],
                "hours_high": figures["hours_high"],
                "session_count": figures["session_count"],
                "source_hours": engine.source_hours(items, day, tz, point_minutes, read_from),
                "insights_included": insights,
            }
            for name in INSIGHT_FIELDS:
                vals[name] = figures.get(name, 0.0) if insights else False
            vals["schedule_known"] = insights and "outside_hours" in figures
            vals.update(self._bf_extra_values(user, day))
            record = self.sudo().search([("user_id", "=", user.id), ("date", "=", day)], limit=1)
            if record:
                record.write(vals)
            else:
                record = self.sudo().create(dict(vals, user_id=user.id, date=day))
            days |= record
        return days

    @api.model
    def _bf_tz(self, user):
        try:
            return ZoneInfo(user.tz or "UTC")
        except (KeyError, ValueError):  # a time zone zoneinfo does not know
            return ZoneInfo("UTC")

    @staticmethod
    def _bf_local_date(moment, tz):
        return moment.replace(tzinfo=timezone.utc).astimezone(tz).date()

    @api.model
    def _bf_extra_values(self, user, day):
        """Hook for bridges: more figures for the day (entered timesheet hours...)."""
        return {}

    @api.model
    def _bf_schedules(self, user, dates, tz):
        """Working intervals of each day, as naive UTC spans; {} if no schedule is known."""
        employee = user.sudo().employee_id
        calendar = employee.resource_calendar_id or user.sudo().company_id.resource_calendar_id
        if not calendar:
            return {}
        start = datetime.combine(dates[0], time.min).replace(tzinfo=tz)
        stop = datetime.combine(dates[-1] + timedelta(days=1), time.min).replace(tzinfo=tz)
        resource = employee.resource_id
        intervals = calendar._work_intervals_batch(start, stop, resources=resource)[resource.id]
        out = {d: [] for d in dates}
        for a, b, _rec in intervals:
            day = a.astimezone(tz).date()
            if day in out:
                out[day].append((a.astimezone(timezone.utc).replace(tzinfo=None),
                                 b.astimezone(timezone.utc).replace(tzinfo=None)))
        return out

    @api.model
    def _cron_bf_compute(self):
        """Hourly: purge first (retention holds even if a computation fails), then the
        last days of every person who turned the feature on, one person at a time."""
        self._bf_purge()
        self.env["bf.work.signal"]._bf_purge()
        self.env.cr.commit()
        Consent = self.env["bf.work.consent"].sudo()
        users = self.env["res.users"].sudo().browse(sorted(Consent._bf_active_user_ids("hours"))).exists()
        for user in users:
            today = datetime.now(self._bf_tz(user)).date()
            try:
                with self.env.cr.savepoint():
                    self._bf_compute(user, [today - timedelta(days=n) for n in range(RECOMPUTE_DAYS)])
            except Exception:
                _logger.exception("Working time: the days of user %s were not computed", user.id)
            self.env.cr.commit()

    @api.model
    def _cron_bf_work_history(self):
        """Take in the earlier days of the people who chose it, a slice at a time,
        going back from the activation of their hours; again at once while days remain."""
        Consent = self.env["bf.work.consent"].sudo()
        deadline = _monotonic() + HISTORY_RUN_SECONDS
        done = remaining = 0
        for consent in Consent.search([("scope", "=", "history"), ("withdrawn_at", "=", False)], order="id"):
            user = consent.user_id
            bounds = self._bf_history_bounds(consent)
            if not user.active or not bounds:
                continue
            first, reached = bounds
            while reached > first and _monotonic() < deadline:
                low = max(first, reached - timedelta(days=HISTORY_SLICE_DAYS))
                try:
                    with self.env.cr.savepoint():
                        self._bf_compute(user, [low + timedelta(days=n) for n in range((reached - low).days)])
                        consent.write({"computed_from": low})
                except Exception:
                    _logger.exception("Working time: the earlier days of user %s were not taken in", user.id)
                    break
                self.env.cr.commit()
                done += (reached - low).days
                reached = low
            remaining += max((reached - first).days, 0)
        self.env["ir.cron"]._notify_progress(done=done, remaining=remaining)

    @api.model
    def _bf_history_bounds(self, consent):
        """(first, reached) of a live "My earlier days": every day from ``reached`` on is
        taken in, and it is all done once ``reached`` is ``first``. None if off.

        ⚠️ Not named ``cursor``: ``_()`` takes a local of that name for the database cursor.
        """
        user = consent.user_id
        since = self.env["bf.work.consent"].sudo()._bf_since(user, "hours")
        if not since or not consent.read_from or consent.withdrawn_at:
            return None
        tz = self._bf_tz(user)
        first = max(self._bf_local_date(consent.read_from, tz), self._bf_first_kept_date())
        # The day of the activation read from its own start until now: it is taken in too.
        reached = consent.computed_from or self._bf_local_date(since, tz) + timedelta(days=1)
        return first, reached

    @api.model
    def _bf_first_kept_date(self):
        """The oldest day kept (bf_work_hours.day_retention_days): older ones are purged."""
        days = int(self.env["ir.config_parameter"].sudo().get_param("bf_work_hours.day_retention_days", 400))
        return fields.Date.today() - timedelta(days=max(days, 1))

    @api.model
    def _bf_forget_before_activation(self, user):
        """Destroy what was taken in from before the activation of the hours (a withdrawal of
        "My earlier days", or the purge after one cut short), then redo the day of the
        activation and the next one, which had read before it."""
        Consent = self.env["bf.work.consent"].sudo()
        since = Consent._bf_since(user, "hours")
        if not since or Consent._bf_history(user):
            return
        tz = self._bf_tz(user)
        first_day = self._bf_local_date(since, tz)
        self.sudo().search([("user_id", "=", user.id), ("date", "<", first_day)]).unlink()
        self.env["bf.work.signal"].sudo().search([("user_id", "=", user.id), ("date_stop", "<", since)]).unlink()
        # The next day too: a stretch from before the activation that ran up to midnight
        # reached it through the margin. Never a day still to come.
        today = datetime.now(tz).date()
        redo = [day for day in (first_day, first_day + timedelta(days=1)) if day <= today]
        try:
            with self.env.cr.savepoint():
                self._bf_compute(user, redo)
        except Exception:
            # The withdrawal goes on, and keeps nothing read before the activation:
            # these days go rather than stay as they were computed.
            _logger.exception("Working time: the days of user %s around the activation were not redone", user.id)
            self.sudo().search([("user_id", "=", user.id), ("date", "in", redo)]).unlink()

    @api.model
    def _bf_purge(self):
        days = int(self.env["ir.config_parameter"].sudo().get_param("bf_work_hours.day_retention_days", 400))
        limit = fields.Date.today() - timedelta(days=max(days, 1))
        self.sudo().search([("date", "<", limit)]).unlink()
        # And what a withdrawal crossed by a running computation, or an archived
        # account, left behind: days of people without hours, indicators of people
        # without indicators, shares of people who are gone.
        Consent = self.env["bf.work.consent"].sudo()
        hours, insights = Consent._bf_active_user_ids("hours"), Consent._bf_active_user_ids("insights")
        self.sudo().search([("user_id", "not in", list(hours))]).unlink()
        self.sudo().search([("user_id", "not in", list(insights)), ("insights_included", "=", True)]).write(
            dict.fromkeys(INSIGHT_FIELDS, False) | {"insights_included": False})
        self.env["bf.work.share"].sudo().search([("active", "=", True), ("owner_id", "not in", list(hours))]
                                                ).action_revoke()
        # Days and traces from before the activation of the hours, kept although
        # "My earlier days" is off (its withdrawal cut short).
        history = Consent._bf_active_user_ids("history")
        for user in self.env["res.users"].sudo().browse(sorted(hours - history)).exists():
            since = Consent._bf_since(user, "hours")
            if since and (self.sudo().search_count([("user_id", "=", user.id),
                                                    ("date", "<", self._bf_local_date(since, self._bf_tz(user)))])
                          or self.env["bf.work.signal"].sudo().search_count([("user_id", "=", user.id),
                                                                             ("date_stop", "<", since)])):
                try:
                    with self.env.cr.savepoint():
                        self._bf_forget_before_activation(user)
                except Exception:
                    _logger.exception("Working time: the earlier days of user %s were not destroyed", user.id)

    def action_bf_recompute(self):
        """Recompute my last days now (button of the person's own view), and say what was done."""
        user = self.env.user
        Consent = self.env["bf.work.consent"]
        if user.id not in Consent._bf_active_user_ids("hours"):
            raise AccessError(_("Turn on “Propose my hours” first, in Working Time > My choices."))
        tz = self._bf_tz(user)
        today = datetime.now(tz).date()
        days = self._bf_compute(user, [today - timedelta(days=n) for n in range(RECOMPUTE_DAYS)])
        Consent = Consent.sudo()
        since = Consent._bf_since(user, "hours")
        history = Consent._bf_history(user)
        lines = [_("%(count)s day(s) recomputed, the last %(max)s at most.", count=len(days), max=RECOMPUTE_DAYS)]
        if not history:
            lines.append(_("Nothing is read before you turned it on, on %(date)s. To take in your earlier "
                           "days: Working Time > My choices.",
                           date=format_date(self.env, self._bf_local_date(since, tz))))
        else:
            first, reached = self._bf_history_bounds(history)
            if reached <= first:
                lines.append(_("Your earlier days are taken in from %(date)s.", date=format_date(self.env, first)))
            else:
                lines.append(_("Your earlier days are being taken in, back to %(date)s: they appear "
                               "a slice at a time.", date=format_date(self.env, first)))
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Working time"),
                "message": " ".join(lines),
                "type": "success",
                "next": {"type": "ir.actions.client", "tag": "soft_reload"},
            },
        }
