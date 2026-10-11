from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.http import request

from .tools import IMPERSONATION_KEY

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
        if self.env.su:
            raise UserError(_("Traces are pushed by the person concerned, not as superuser."))
        if request and request.session.get(IMPERSONATION_KEY):
            raise AccessError(_("Not while being impersonated."))
        user = self.env.user
        if user.id not in self.env["bf.work.consent"]._bf_active_user_ids("hours"):
            raise AccessError(_("Turn on “Propose my hours” first, in Working Time > My choices."))
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
            if start < fields.Datetime.now() - PUSH_MAX_AGE:
                raise UserError(_("A pushed trace is at most %s hours old.", int(PUSH_MAX_AGE.total_seconds() // 3600)))
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
        return len(new)

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
