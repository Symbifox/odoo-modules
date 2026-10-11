import logging
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from odoo import _, api, fields, models
from odoo.exceptions import AccessError

from . import engine

# Figures that describe HOW one works rather than how long: computed and kept
# only for people who also activated "My indicators".
INSIGHT_FIELDS = (
    "first_activity", "last_activity", "span_hours", "longest_session_hours",
    "meeting_hours", "back_to_back_meetings", "outside_hours", "schedule_known",
)
RECOMPUTE_DAYS = 7

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
        """(Re)compute the days of one person. Days before the activation are never read."""
        Consent = self.env["bf.work.consent"].sudo()
        since = Consent._bf_since(user, "hours")
        if not since:
            return self.browse()
        tz = self._bf_tz(user)
        dates = sorted(d for d in set(dates) if d >= self._bf_local_date(since, tz))
        if not dates:
            return self.browse()
        insights_since = Consent._bf_since(user, "insights")
        insights_from = self._bf_local_date(insights_since, tz) if insights_since else None
        gap = self._bf_gap_minutes()
        margin = timedelta(minutes=max(gap, engine.HIGH_GAP) + 60)
        start = max(engine.local_day_bounds(dates[0], tz)[0] - margin, since)
        stop = engine.local_day_bounds(dates[-1], tz)[1] + margin
        collector = self.env["bf.work.collector"].sudo()
        # Nothing before the activation is read, not even the margin of the first day.
        items = engine.not_before(collector._bf_collect(user, start, stop), since)
        point_minutes = collector._bf_point_minutes()
        insight_dates = [d for d in dates if insights_from and d >= insights_from]
        schedules = self._bf_schedules(user, insight_dates, tz) if insight_dates else {}
        days = self.browse()
        for day in dates:
            insights = day in insight_dates
            figures = engine.day_figures(items, day, tz, gap, point_minutes, schedules.get(day), since)
            vals = {
                "gap_minutes": gap,
                "hours": figures["hours"],
                "hours_low": figures["hours_low"],
                "hours_high": figures["hours_high"],
                "session_count": figures["session_count"],
                "source_hours": engine.source_hours(items, day, tz, point_minutes, since),
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

    def action_bf_recompute(self):
        """Recompute my last days now (button of the person's own view)."""
        user = self.env.user
        if user.id not in self.env["bf.work.consent"]._bf_active_user_ids("hours"):
            raise AccessError(_("Turn on “Propose my hours” first, in Working Time > My choices."))
        today = datetime.now(self._bf_tz(user)).date()
        self._bf_compute(user, [today - timedelta(days=n) for n in range(RECOMPUTE_DAYS)])
        return {"type": "ir.actions.client", "tag": "reload"}
