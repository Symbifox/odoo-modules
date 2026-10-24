from datetime import datetime, timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.http import request

from .tools import IMPERSONATION_KEY
from .work_day import RECOMPUTE_DAYS

# Two browser heartbeats closer than this extend the same stretch. The web
# client reports every minute; three minutes absorbs a late report.
PRESENCE_MERGE = timedelta(minutes=3)
# A heartbeat counts only if the last keyboard or mouse input is this recent.
PRESENCE_MAX_INACTIVITY_MS = 2 * 60 * 1000
# Sources a person may push themselves (from a local tool).
PUSHABLE_SOURCES = ("terminal",)
PUSH_MAX = 5000
# A pushed trace is a moment or a short stretch, never "since Monday".
PUSH_MAX_SPAN = timedelta(hours=4)
# A local tool reports what just happened, not the history of a year.
PUSH_MAX_AGE = timedelta(hours=48)


class WorkSignal(models.Model):
    """A trace that proves a hand: kept only for people who activated the feature.

    Only the traces that are captured live are stored here (browser activity,
    a timer segment, a local terminal). The others (a meeting, a prompt, a
    note, an email) already live in their own model and are read when the day
    is computed (``bf.work.collector``).

    Raw traces are personal and short-lived: readable by their owner only,
    never shared, purged after ``bf_work_hours.signal_retention_days``.
    """

    _name = "bf.work.signal"
    _description = "Working time: trace"
    _order = "date_start desc, id desc"
    # bf_impersonate hides these from whoever impersonates the person.
    _gen_scope = "bf_work_hours"

    user_id = fields.Many2one("res.users", required=True, readonly=True, index=True, ondelete="cascade")
    source = fields.Selection(
        [("presence", "Browser activity"), ("terminal", "Terminal")],
        required=True, readonly=True,
    )
    date_start = fields.Datetime(required=True, readonly=True, index=True)
    date_stop = fields.Datetime(required=True, readonly=True)

    _sql_constraints = [
        ("stop_after_start", "CHECK (date_stop >= date_start)", "A trace cannot end before it starts."),
    ]

    @api.model
    def _bf_record_presence(self, user_id, moment):
        """Extend the person's current stretch of browser activity, or open one."""
        last = self.sudo().search([
            ("user_id", "=", user_id), ("source", "=", "presence"),
            ("date_stop", ">=", moment - PRESENCE_MERGE),
        ], order="date_stop desc", limit=1)
        if last:
            if moment > last.date_stop:
                last.sudo().write({"date_stop": moment})
            return last
        return self.sudo().create({
            "user_id": user_id, "source": "presence", "date_start": moment, "date_stop": moment,
        })

    @api.model
    def bf_work_push(self, signals):
        """Record traces pushed by the person's own local tool (RPC).

        ``signals``: list of {"source", "start", "stop"} in UTC
        ("YYYY-MM-DD HH:MM:SS"). For the CALLER only, and only if they
        activated "Propose my hours". Already known traces are skipped.
        Returns the number of traces added.
        """
        user = self._bf_push_caller()
        oldest = self._bf_push_oldest(user)
        if len(signals) > PUSH_MAX:
            raise UserError(_("At most %s traces per call.", PUSH_MAX))
        rows = []
        for signal in signals:
            source = signal.get("source")
            if source not in PUSHABLE_SOURCES:
                raise UserError(_("Unknown source: %s", source))
            start = fields.Datetime.to_datetime(signal["start"])
            stop = fields.Datetime.to_datetime(signal.get("stop") or signal["start"])
            if stop < start or stop > fields.Datetime.now() + timedelta(minutes=5):
                raise UserError(_("A trace must end after it starts, and not in the future."))
            if start < oldest:
                raise UserError(_("A pushed trace starts on %(oldest)s UTC at the earliest (earlier days: only "
                                  "if you chose to take them in).", oldest=fields.Datetime.to_string(oldest)))
            if stop - start > PUSH_MAX_SPAN:
                raise UserError(_("A pushed trace lasts at most %s hours.", PUSH_MAX_SPAN.seconds // 3600))
            rows.append((source, start, stop))
        if not rows:
            return 0
        known = set(self.sudo().search([
            ("user_id", "=", user.id), ("source", "in", list(PUSHABLE_SOURCES)),
            ("date_start", ">=", min(r[1] for r in rows)), ("date_start", "<=", max(r[1] for r in rows)),
        ]).mapped(lambda s: (s.source, s.date_start)))
        new = [r for r in set(rows) if (r[0], r[1]) not in known]
        self.sudo().create([
            {"user_id": user.id, "source": s, "date_start": a, "date_stop": b} for s, a, b in new
        ])
        # Earlier days the hourly job no longer redoes: redo those the new traces touch.
        Day = self.env["bf.work.day"].sudo()
        tz = Day._bf_tz(user)
        recent = datetime.now(tz).date() - timedelta(days=RECOMPUTE_DAYS)
        old_days = {Day._bf_local_date(d, tz) for _s, a, b in new for d in (a, b)} - {None}
        old_days = sorted(d for d in old_days if d < recent)
        if old_days:
            Day._bf_compute(user, old_days)
        return len(new)

    @api.model
    def bf_work_push_window(self):
        """For the person's own local tool: whether it may push, and the earliest
        moment a trace may start (UTC), so that it sends no more than that."""
        user = self._bf_push_caller(check_enabled=False)
        if user.id not in self.env["bf.work.consent"]._bf_active_user_ids("hours"):
            return {"enabled": False, "earliest": False}
        return {"enabled": True, "earliest": fields.Datetime.to_string(self._bf_push_oldest(user))}

    @api.model
    def _bf_push_caller(self, check_enabled=True):
        """The person pushing: themselves, never as superuser, never under an impersonation."""
        if self.env.su:
            raise UserError(_("Traces are pushed by the person concerned, not as superuser."))
        if request and request.session.get(IMPERSONATION_KEY):
            raise AccessError(_("Not while being impersonated."))
        user = self.env["res.users"].browse(self.env.uid)
        if check_enabled and user.id not in self.env["bf.work.consent"]._bf_active_user_ids("hours"):
            raise AccessError(_("Turn on “Propose my hours” first, in Working Time > My choices."))
        return user

    @api.model
    def _bf_push_oldest(self, user):
        """The earliest start of a pushed trace.

        🔴 Never before the start of reading: a trace from before the activation
        is not collected at all, not merely left unread (18.0.1.0.2 kept 48 hours
        of them). Within that: 48 hours back, or, for the earlier days the person
        chose to take in, as far as the raw traces are kept.
        """
        Consent = self.env["bf.work.consent"].sudo()
        now = fields.Datetime.now()
        read_from = Consent._bf_read_from(user) or now
        if Consent._bf_history(user):
            days = int(self.env["ir.config_parameter"].sudo().get_param("bf_work_hours.signal_retention_days", 60))
            return max(read_from, now - timedelta(days=max(days, 1)))
        return max(read_from, now - PUSH_MAX_AGE)

    @api.model
    def _bf_purge(self):
        """Destroy the raw traces older than the retention (daily job)."""
        days = int(self.env["ir.config_parameter"].sudo().get_param(
            "bf_work_hours.signal_retention_days", 60))
        limit = fields.Datetime.now() - timedelta(days=max(days, 1))
        self.sudo().search([("date_stop", "<", limit)]).unlink()
        # And every trace of a person who no longer has the feature on (a withdrawal
        # that failed half way, an archived account).
        active = self.env["bf.work.consent"].sudo()._bf_active_user_ids("hours")
        self.sudo().search([("user_id", "not in", list(active))]).unlink()
