"""A route day: one worker, one date, the stops in order, and the proof at each stop.

⚠️ The worker's phone calls the ``app_*`` methods. The worker has READ access only
(record rules: their own days); every write goes through a method that checks the
day is theirs, checks each value, then writes with sudo. A plain RPC ``write``
from the phone is refused by the access rights. The worker is not made a follower of
their days (no assignment message, see bf.route); like any reader, they could still
follow a day themselves and post in its chatter.

⚠️ A stop is marked once. Marking it again from the phone is refused, except the
very same mark sent twice (same key): a mark is proof, and the second one would
overwrite its time and position, or sell twice.

⚠️ Position (Quebec Law 25, s. 8.1): read once when a stop is marked, and kept only
when the company turned it on AND the worker acknowledged the current notice.
Erased after the retention period by the watcher.
"""
import re
from datetime import timedelta

import pytz
from markupsafe import Markup, escape

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from .tools import local_to_utc, local_today, parse_client_time, utc_to_local

DAY_STATES = [
    ("planned", "Planned"),
    ("in_progress", "On the road"),
    ("done", "Done"),
    ("canceled", "Cancelled"),
]
STOP_STATES = [
    ("todo", "To do"),
    ("done", "Done"),
    ("absent", "Customer absent"),
    ("postponed", "Postponed"),
    ("missed", "Missed"),
]
# What the phone may set. "missed" belongs to the end of the day, "todo" to the office.
APP_STOP_STATES = {"done", "absent", "postponed"}
NOTE_MAX = 2000
# More than this between two odometer readings of one day is a typing error.
MAX_KM_PER_DAY = 2000
# A start odometer this far above the vehicle's last reading is a typing error.
MAX_KM_SINCE_LAST = 10_000
# No road vehicle reaches this: a reading above is a typing error, even on a new vehicle.
MAX_ODOMETER = 3_000_000
# A phone reports at most this many refused marks per day; more is flooding.
MAX_REFUSALS_PER_DAY = 50


def _valid_tz(name):
    return isinstance(name, str) and name in pytz.all_timezones_set


def clean_env(records):
    """The records with a fresh context: only the language, the time zone and the companies.

    ⚠️ The phone calls the ``app_*`` methods over RPC with a context it chooses. Odoo
    drops only the ``default_*`` keys when switching to sudo: a key like
    ``skip_invoice_sync`` or ``tracking_disable`` would reach the documents these
    methods create in sudo (a delivery without its invoice, a day without its trace).
    """
    context = records.env.context
    keep = {key: context[key] for key in ("lang", "tz", "allowed_company_ids") if key in context}
    # A time zone or a language the phone made up would fail deep inside a document.
    if "tz" in keep and not _valid_tz(keep["tz"]):
        del keep["tz"]
    if "lang" in keep and not records.env["res.lang"]._lang_get(keep["lang"]):
        del keep["lang"]
    return records.with_env(records.env(context=keep))


# Fields of a day the office sets by hand: once one is changed, the route's template
# no longer overwrites the day (see bf.route._refresh_upcoming_days).
OFFICE_FIELDS = {"user_id", "vehicle_id", "date", "date_planned_start", "stop_ids"}
# A mark sent this long after it was made is flagged "sent later".
OFFLINE_AFTER = timedelta(minutes=2)


class BfRouteDay(models.Model):
    _name = "bf.route.day"
    _description = "Route day"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "date desc, date_planned_start, id"

    name = fields.Char(compute="_compute_name", store=True)
    route_id = fields.Many2one("bf.route", string="Route", required=True, ondelete="restrict",
                               index=True)
    company_id = fields.Many2one(related="route_id.company_id", store=True, index=True)
    responsible_id = fields.Many2one(related="route_id.responsible_id", store=True)
    date = fields.Date(required=True, index=True, default=fields.Date.context_today)
    recurrence_date = fields.Date(
        readonly=True, copy=False, index=True,
        help="The date the route's calendar made this day for. A day moved by hand keeps it, "
             "so it is not made again.")
    edited = fields.Boolean(
        string="Edited by hand", readonly=True, copy=False,
        help="The office changed this day: changes to the route no longer overwrite it.")
    user_id = fields.Many2one("res.users", string="Worker", index=True, tracking=True)
    vehicle_id = fields.Many2one("fleet.vehicle", string="Vehicle", tracking=True)
    heavy_vehicle = fields.Boolean(related="vehicle_id.bf_route_heavy")
    date_planned_start = fields.Datetime(string="Planned start")
    state = fields.Selection(DAY_STATES, default="planned", required=True, index=True,
                             tracking=True)
    date_started = fields.Datetime(string="Started", readonly=True, copy=False)
    date_finished = fields.Datetime(string="Finished", readonly=True, copy=False)
    odometer_start = fields.Float(string="Odometer at start", copy=False)
    odometer_end = fields.Float(string="Odometer at end", copy=False)
    distance_actual_km = fields.Float(string="Distance driven", compute="_compute_distance",
                                      store=True, digits=(10, 1))
    distance_planned_km = fields.Float(string="Planned distance (km)", readonly=True,
                                       digits=(10, 1), copy=False)
    duration_planned_minutes = fields.Integer(string="Planned driving time (min)", readonly=True,
                                              copy=False)
    safety_check_at = fields.Datetime(string="Pre-trip inspection at", readonly=True, copy=False)
    safety_check_by = fields.Many2one("res.users", string="Pre-trip inspection by", readonly=True,
                                      copy=False)
    stop_ids = fields.One2many("bf.route.day.stop", "day_id", string="Stops", copy=True)
    stop_count = fields.Integer(compute="_compute_progress")
    progress = fields.Char(compute="_compute_progress")
    alert_late_sent = fields.Boolean(readonly=True, copy=False)
    alert_open_sent = fields.Boolean(readonly=True, copy=False)
    refusal_keys = fields.Text(readonly=True, copy=False,
                               help="Keys of the refused marks the phone reported, so each counts once.")

    _sql_constraints = [
        ("route_date_unique", "UNIQUE(route_id, date)",
         "A route has one day per date. Add the stop to the existing day instead."),
    ]

    def _message_auto_subscribe_followers(self, updated_values, default_subtype_ids):
        """No assignment message for the worker: see bf.route."""
        return []

    @api.depends("route_id.name", "date")
    def _compute_name(self):
        for day in self:
            day.name = "%s · %s" % (day.route_id.name or "", day.date or "")

    @api.depends("odometer_start", "odometer_end")
    def _compute_distance(self):
        for day in self:
            if day.odometer_start and day.odometer_end and day.odometer_end >= day.odometer_start:
                day.distance_actual_km = day.odometer_end - day.odometer_start
            else:
                day.distance_actual_km = 0.0

    @api.depends("stop_ids.state")
    def _compute_progress(self):
        for day in self:
            marked = day.stop_ids.filtered(lambda s: s.state != "todo")
            day.stop_count = len(day.stop_ids)
            day.progress = "%s / %s" % (len(marked), len(day.stop_ids))

    @api.model_create_multi
    def create(self, vals_list):
        Route = self.env["bf.route"]
        for vals in vals_list:
            # A day planned by hand belongs to the office from the start: the template never
            # rewrites it. Only the days the calendar makes follow the template.
            if not self.env.context.get("bf_route_auto"):
                vals.setdefault("edited", True)
            route = Route.browse(vals.get("route_id"))
            if not route:
                continue
            day = fields.Date.to_date(vals.get("date")) or fields.Date.context_today(self)
            vals.setdefault("user_id", route.user_id.id)
            vals.setdefault("vehicle_id", route.vehicle_id.id)
            if not vals.get("date_planned_start"):
                vals["date_planned_start"] = local_to_utc(route._tz(), day, route.start_hour)
            if self.env.context.get("bf_route_copy_stops") and not vals.get("stop_ids"):
                vals["stop_ids"] = route._stop_vals_for_day()
        return super(BfRouteDay, self.with_context(bf_route_copy_stops=False)).create(vals_list)

    def write(self, vals):
        if OFFICE_FIELDS & set(vals) and not self.env.context.get("bf_route_auto"):
            vals = dict(vals, edited=True)
        return super().write(vals)

    # ------------------------------------------------------------------
    # Rights
    # ------------------------------------------------------------------
    def _is_manager(self):
        return self.env.user.has_group("bf_route.group_route_manager")

    def _check_driver(self):
        """The worker of the day, or a route manager. Anyone else is refused."""
        if self.env.su or self._is_manager():
            return
        for day in self:
            if day.user_id != self.env.user:
                raise AccessError(_("This route day is not yours."))

    def _check_manager(self):
        if not (self.env.su or self._is_manager()):
            raise AccessError(_("Only a route manager can do this."))

    # ------------------------------------------------------------------
    # The day
    # ------------------------------------------------------------------
    def action_start(self, odometer=None, safety_check=False, client_time=None):
        self = clean_env(self)
        self._check_driver()
        now = fields.Datetime.now()
        for day in self:
            if day.state != "planned":
                raise UserError(_("Only a planned day can start."))
            vehicle = day.vehicle_id.sudo()
            if day.heavy_vehicle and not safety_check:
                raise UserError(_(
                    "%s is a heavy vehicle: do the pre-trip inspection before leaving.",
                    vehicle.display_name))
            earliest = local_to_utc(day.route_id.sudo()._tz(), day.date, 0.0) - timedelta(hours=12)
            started = parse_client_time(client_time, now, earliest) or now
            vals = {"state": "in_progress", "date_started": started}
            if odometer:
                vals["odometer_start"] = day._clean_odometer(odometer)
                last = vehicle.odometer if vehicle else 0.0
                if last and vals["odometer_start"] - last > MAX_KM_SINCE_LAST:
                    raise UserError(_("The odometer at start is %(km)s km above the vehicle's last "
                                      "reading: check it.", km=round(vals["odometer_start"] - last)))
            if safety_check:
                vals.update(safety_check_at=started, safety_check_by=self.env.user.id)
            day.sudo().with_context(bf_route_auto=True).write(vals)
            last = vehicle.odometer if vehicle else 0.0
            if odometer and last and vals["odometer_start"] < last:
                day.sudo().message_post(body=_(
                    "Odometer at start (%(start)s) is below the vehicle's last reading (%(last)s).",
                    start=vals["odometer_start"], last=last))
        return True

    def action_finish(self, odometer=None, client_time=None):
        self = clean_env(self)
        self._check_driver()
        now = fields.Datetime.now()
        for day in self:
            if day.state != "in_progress":
                raise UserError(_("Only a day on the road can finish."))
            vals = {"state": "done",
                    "date_finished": parse_client_time(client_time, now, day.date_started) or now}
            if odometer:
                vals["odometer_end"] = day._clean_odometer(odometer)
                if day.odometer_start and vals["odometer_end"] < day.odometer_start:
                    raise UserError(_("The odometer at the end is below the one at the start."))
                # Measured from the start of the day, or else from the vehicle's last reading.
                reference = day.odometer_start or (day.vehicle_id.sudo().odometer if day.vehicle_id else 0.0)
                if reference and vals["odometer_end"] - reference > MAX_KM_PER_DAY:
                    raise UserError(_("More than %s km in one day: check the odometer.", MAX_KM_PER_DAY))
            day.sudo().with_context(bf_route_auto=True).write(vals)
            missed = day.stop_ids.filtered(lambda s: s.state == "todo")
            if missed:
                missed.sudo().with_context(bf_route_auto=True).write({"state": "missed"})
                day._alert(
                    lambda e: e._("Stops missed: %s", day.name),
                    lambda e: e._("%(who)s finished the day without: %(stops)s.",
                                  who=day.user_id.name or "-",
                                  stops=", ".join(missed.mapped("partner_id.display_name"))))
            if day.vehicle_id and day.odometer_end:
                self.env["fleet.vehicle.odometer"].sudo().create({
                    "vehicle_id": day.vehicle_id.id,
                    "value": day.odometer_end,
                    "date": day.date,
                })
        return True

    def action_cancel(self):
        """Only a planned day: a day on the road is finished, so the marks the phone has not
        sent yet still find their day."""
        self._check_manager()
        if self.filtered(lambda d: d.state != "planned"):
            raise UserError(_("Only a planned day can be cancelled. A day on the road is finished."))
        self.write({"state": "canceled"})

    def action_reset(self):
        self._check_manager()
        self.filtered(lambda d: d.state == "canceled").write({"state": "planned"})

    @staticmethod
    def _clean_odometer(value):
        try:
            value = float(value)
        except (TypeError, ValueError):
            raise ValidationError(_("The odometer must be a number."))
        if value != value or value < 0 or value > MAX_ODOMETER:
            raise ValidationError(_("The odometer must be between 0 and %s.", MAX_ODOMETER))
        return value

    # ------------------------------------------------------------------
    # Routing engine
    # ------------------------------------------------------------------
    def _remaining(self):
        self.ensure_one()
        return self.stop_ids.filtered(lambda s: s.state == "todo").sorted("sequence")

    def action_compute_plan(self):
        """Planned distance and time, and the planned time of each remaining stop."""
        self._check_manager()
        Engine = self.env["bf.route.engine"]
        for day in self:
            stops = day._remaining()
            if not stops:
                raise UserError(_("No stop left to plan."))
            route = day.route_id
            start = route.start_partner_id or route.company_id.partner_id
            end = route.end_partner_id or start
            plan = Engine._route(Engine._points(start, stops.mapped("partner_id"), end))
            if day.state == "in_progress" or not day.date_planned_start:
                clock = fields.Datetime.now()
            else:
                clock = day.date_planned_start
            for stop, leg in zip(stops, plan["legs"]):
                clock += timedelta(seconds=leg["duration"])
                if stop.window_start:
                    opens = local_to_utc(route._tz(), day.date, stop.window_start)
                    clock = max(clock, opens)
                stop.with_context(bf_route_auto=True).planned_time = clock.replace(second=0, microsecond=0)
                clock += timedelta(minutes=stop.service_minutes)
            day.with_context(bf_route_auto=True).write({
                "distance_planned_km": plan["distance"] / 1000.0,
                "duration_planned_minutes": round(plan["duration"] / 60.0)})
        return True

    def action_optimize(self):
        self._check_manager()
        unassigned = {}
        for day in self:
            stops = day._remaining()
            if len(stops) < 2:
                raise UserError(_("There is nothing to reorder with fewer than two stops left."))
            route = day.route_id
            start = route.start_partner_id or route.company_id.partner_id
            end = route.end_partner_id or start
            local_start = utc_to_local(route._tz(), day.date_planned_start or fields.Datetime.now())
            seconds = local_start.hour * 3600 + local_start.minute * 60
            order = self.env["bf.route.engine"]._optimize(start, end, stops, start_seconds=seconds)
            first = min(stops.mapped("sequence"))
            for rank, stop in enumerate(order["ordered"]):
                stop.sequence = first + rank
            unassigned[day.id] = order["unassigned"]
        self.action_compute_plan()
        for day in self:
            day.sudo().message_post(body=self.env["bf.route.engine"]._optimized_note(
                day.distance_planned_km, day.duration_planned_minutes, unassigned[day.id]))
        return True

    # ------------------------------------------------------------------
    # Alerts
    # ------------------------------------------------------------------
    def _alert(self, summary, note):
        """One activity for the person responsible, written in THEIR language.

        ``summary`` and ``note`` take an environment and return the text, so the
        text is translated for the reader, not for whoever (or whatever cron) is
        running: a cron has no language of its own.
        """
        self.ensure_one()
        reader = self.responsible_id or self.env.user
        env = self.with_context(lang=reader.lang).env
        todo = self.env.ref("mail.mail_activity_data_todo", raise_if_not_found=False)
        # ⚠️ escape(): the note may carry what the worker typed (a reference, a note).
        self.sudo().with_context(lang=reader.lang).activity_schedule(
            activity_type_id=todo.id if todo else False, user_id=reader.id,
            summary=summary(env), note=escape(note(env)))

    @api.model
    def _late_minutes(self, company):
        return company.bf_route_late_minutes or 30

    @api.model
    def _watch(self):
        now = fields.Datetime.now()
        # 1. Days that should have started.
        for day in self.sudo().search([("state", "=", "planned"), ("alert_late_sent", "=", False),
                                       ("date_planned_start", "!=", False),
                                       ("date_planned_start", "<", now)]):
            if day.date_planned_start + timedelta(minutes=self._late_minutes(day.company_id)) > now:
                continue
            day.alert_late_sent = True
            when = utc_to_local(day.route_id._tz(), day.date_planned_start).strftime("%H:%M")
            day._alert(lambda e: e._("Route not started: %s", day.name),
                       lambda e: e._("%(who)s has not started the route planned at %(when)s.",
                                     who=day.user_id.name or e._("Nobody"), when=when))
        # 2. Days still on the road after their date.
        for day in self.sudo().search([("state", "=", "in_progress"),
                                       ("alert_open_sent", "=", False)]):
            if local_today(day.route_id._tz(), now) <= day.date:
                continue
            day.alert_open_sent = True
            day._alert(lambda e: e._("Route day left open: %s", day.name),
                       lambda e: e._("%(who)s did not finish the day. %(progress)s stops marked.",
                                     who=day.user_id.name or "-", progress=day.progress))

    @api.model
    def _notify(self, message, kind="info"):
        return {"type": "ir.actions.client", "tag": "display_notification",
                "params": {"message": message, "type": kind, "sticky": False}}

    # ------------------------------------------------------------------
    # The worker's phone ("My route")
    # ------------------------------------------------------------------
    def _office_prepared(self):
        """True when the office worked on this planned day: edited by hand, or (satellites) a
        truck already loaded. The route's template never rewrites nor empties such a day."""
        self.ensure_one()
        return self.edited

    def _keep_when_dropped(self):
        """True when this planned day holds something the calendar did not put there (a stop
        postponed from another day). Satellites add their own reasons (a loaded truck)."""
        self.ensure_one()
        return bool(self.stop_ids.filtered(lambda s: s.origin == "postponed"))

    def app_report_refusal(self, key, stop_id=None, label=None, reason=None, state=None, note=None,
                           **extra):
        """The phone tells the office that one of its marks was refused, with what it carried.
        On the DAY, not the stop: the stop may be the very thing the office deleted.
        Sent twice (same key), it counts once; at most 50 a day; one activity per day."""
        self = clean_env(self)
        self.ensure_one()
        self._check_driver()
        day = self.sudo()
        key = str(key or "")
        if not re.fullmatch(r"[A-Za-z0-9-]{1,64}", key):
            return False
        seen = (day.refusal_keys or "").split()
        if key in seen:
            return True
        if len(seen) >= MAX_REFUSALS_PER_DAY:
            return False  # the phone keeps it listed and asks the worker to call
        day.with_context(bf_route_auto=True).refusal_keys = " ".join(seen + [key])
        Stop = self.env["bf.route.day.stop"]
        stop = Stop.sudo().browse(int(stop_id)).exists() if str(stop_id or "").isdigit() else Stop
        if stop and stop.day_id != day:
            stop = Stop
        who = stop.partner_id.display_name if stop else _(
            "%s (name sent by the phone, stop removed)", str(label or "?")[:120])
        carried = [str(state or "")[:20], str(note or "")[:NOTE_MAX]] + Stop._refusal_details(extra, stop)
        lines = [_("A mark from the phone was refused: %s", str(reason or "")[:500]),
                 _("It carried: %s", " · ".join(part for part in carried if part))]
        day.message_post(body=Markup("<p><b>%s</b></p><ul>%s</ul>") % (
            who, Markup("").join(Markup("<li>%s</li>") % line for line in lines)))
        summary = day.with_context(lang=(day.responsible_id or self.env.user).lang).env._(
            "Marks refused: %s", day.name)
        open_activity = day.activity_ids.filtered(lambda a: a.summary == summary)[:1]
        if open_activity:
            open_activity.note = open_activity.note + Markup("<br/>") + escape(" ".join([who] + lines))
        else:
            day._alert(lambda e: e._("Marks refused: %s", day.name), lambda e: " ".join([who] + lines))
        return True

    @api.model
    def app_load(self):
        """Everything the phone shows: today's days of the worker (and any day still open),
        and whether the position notice must be read first."""
        self = clean_env(self)
        user = self.env.user
        company = self.env.company
        tz_day = local_today(self.env["bf.route"]._tz_for(user))
        days = self.search([("user_id", "=", user.id), "|",
                            "&", ("date", "=", tz_day), ("state", "in", ("planned", "in_progress")),
                            ("state", "=", "in_progress")], order="date, date_planned_start")
        notice = self.env["bf.route.notice.ack"]._status(company)
        return {
            "today": fields.Date.to_string(tz_day),
            "notice": notice,
            "days": [day._app_data() for day in days],
        }

    def _app_data(self):
        self.ensure_one()
        tz = self.route_id.sudo()._tz()
        vehicle = self.vehicle_id.sudo()

        def local(value):
            return utc_to_local(tz, value).strftime("%H:%M") if value else False

        return {
            "id": self.id,
            "name": self.route_id.sudo().name,
            "date": fields.Date.to_string(self.date),
            "state": self.state,
            "planned_start": local(self.date_planned_start),
            "vehicle": vehicle.display_name or False,
            "heavy_vehicle": bool(vehicle.bf_route_heavy),
            "last_odometer": vehicle.odometer if vehicle else False,
            "odometer_start": self.odometer_start or False,
            "stops": [stop._app_data(local) for stop in self.stop_ids.sorted("sequence")],
        }

    def app_start(self, odometer=None, safety_check=False, client_time=None):
        self = clean_env(self)
        self.ensure_one()
        self.action_start(odometer=odometer, safety_check=bool(safety_check),
                          client_time=client_time)
        return self._app_data()

    def app_finish(self, odometer=None, client_time=None):
        self = clean_env(self)
        self.ensure_one()
        self.action_finish(odometer=odometer, client_time=client_time)
        return self._app_data()


class BfRouteDayStop(models.Model):
    _name = "bf.route.day.stop"
    _description = "Stop of a route day"
    _order = "day_id, sequence, id"

    day_id = fields.Many2one("bf.route.day", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="day_id.company_id", store=True)
    user_id = fields.Many2one(related="day_id.user_id", store=True, string="Worker")
    date = fields.Date(related="day_id.date", store=True)
    template_stop_id = fields.Many2one("bf.route.stop", string="From the route", ondelete="set null",
                                       readonly=True)
    origin = fields.Selection(
        [("template", "Route"), ("manual", "Added by hand"), ("postponed", "Postponed")],
        default="manual", required=True, readonly=True, copy=False,
        help="Where the stop comes from. The route's template replaces only its own copies.")
    sequence = fields.Integer(default=100)
    partner_id = fields.Many2one("res.partner", string="Customer", required=True)
    address = fields.Char(related="partner_id.contact_address", string="Address")
    window_start = fields.Float(string="Not before")
    window_end = fields.Float(string="Not after")
    service_minutes = fields.Integer(string="Time on site (min)", default=10)
    instructions = fields.Text(string="Access instructions")
    planned_time = fields.Datetime(string="Planned at", copy=False)
    planned_clock = fields.Char(string="Planned time", compute="_compute_clocks",
                                help="Local time, in the route's time zone.")
    state = fields.Selection(STOP_STATES, default="todo", required=True, index=True, copy=False)
    done_at = fields.Datetime(string="Marked at", readonly=True, copy=False)
    done_by = fields.Many2one("res.users", string="Marked by", readonly=True, copy=False)
    done_clock = fields.Char(string="Marked time", compute="_compute_clocks",
                             help="Local time, in the route's time zone.")
    note = fields.Text(copy=False)
    sent_later = fields.Boolean(string="Sent later", readonly=True, copy=False,
                                help="Marked without network and sent when it came back.")
    app_key = fields.Char(readonly=True, copy=False, index=True,
                          help="Key of the last mark sent by the phone; a resend is ignored.")
    latitude = fields.Float(digits=(10, 7), readonly=True, copy=False,
                            groups="bf_route.group_route_manager")
    longitude = fields.Float(digits=(10, 7), readonly=True, copy=False,
                             groups="bf_route.group_route_manager")
    accuracy = fields.Float(string="Accuracy (m)", readonly=True, copy=False,
                            groups="bf_route.group_route_manager")
    position_erased = fields.Boolean(readonly=True, copy=False,
                                     groups="bf_route.group_route_manager",
                                     help="The position was erased at the end of the retention period.")

    def _compute_display_name(self):
        for stop in self:
            stop.display_name = "%s · %s" % (stop.day_id.name or "", stop.partner_id.display_name)

    # A stop added, changed or removed by the office makes its planned day "edited by hand".
    def _flag_edited(self, days):
        if not self.env.context.get("bf_route_auto"):
            days.filtered(lambda d: d.state == "planned" and not d.edited).sudo().with_context(
                bf_route_auto=True).write({"edited": True})

    @api.model_create_multi
    def create(self, vals_list):
        stops = super().create(vals_list)
        self._flag_edited(stops.day_id)
        return stops

    def write(self, vals):
        result = super().write(vals)
        self._flag_edited(self.day_id)
        return result

    def unlink(self):
        days = self.day_id
        result = super().unlink()
        self._flag_edited(days.exists())
        return result

    def _app_data(self, local):
        self.ensure_one()
        partner = self.partner_id.sudo()
        return {
            "id": self.id,
            "state": self.state,
            "partner": partner.display_name,
            "address": ", ".join(p for p in (partner.street, partner.street2, partner.city) if p),
            "phone": partner.mobile or partner.phone or False,
            "latitude": partner.partner_latitude or False,
            "longitude": partner.partner_longitude or False,
            "window": self._window_label(),
            "planned_time": local(self.planned_time),
            "instructions": self.instructions or False,
            "note": self.note or False,
            "done_at": local(self.done_at),
        }

    def _window_label(self):
        def hm(value):
            whole = int(value)
            return "%02d:%02d" % (whole, round((value - whole) * 60) % 60)

        if self.window_start and self.window_end:
            return "%s – %s" % (hm(self.window_start), hm(self.window_end))
        if self.window_start:
            return _("from %s", hm(self.window_start))
        if self.window_end:
            return _("until %s", hm(self.window_end))
        return False

    @staticmethod
    def _clean_position(position):
        """{latitude, longitude, accuracy} with sane values, or None."""
        if not isinstance(position, dict):
            return None
        try:
            lat = float(position.get("latitude"))
            lon = float(position.get("longitude"))
            acc = float(position.get("accuracy") or 0.0)
        except (TypeError, ValueError):
            return None
        if not (-90 <= lat <= 90 and -180 <= lon <= 180) or not 0 <= acc <= 100_000:
            return None
        return {"latitude": lat, "longitude": lon, "accuracy": acc}

    def _app_local(self):
        tz = self.day_id.route_id.sudo()._tz()
        return lambda value: value and utc_to_local(tz, value).strftime("%H:%M")

    @api.depends("planned_time", "done_at", "day_id.route_id")
    def _compute_clocks(self):
        """The time alone, as the day's list shows it: the date is the day's."""
        for stop in self:
            route = stop.day_id.route_id.sudo()
            tz = route._tz() if route else pytz.utc
            stop.planned_clock = utc_to_local(tz, stop.planned_time).strftime("%H:%M") \
                if stop.planned_time else False
            stop.done_clock = utc_to_local(tz, stop.done_at).strftime("%H:%M") \
                if stop.done_at else False

    def _is_resend(self, app_key):
        self.ensure_one()
        return bool(app_key) and self.app_key == str(app_key)[:64]

    def app_mark(self, state, note=None, position=None, client_time=None, app_key=None, **extra):
        """Mark one stop from the phone. Safe to send twice: the same ``app_key`` is ignored.

        ``extra`` carries what satellites add to a mark (a sale at the stop, for
        instance); without the satellite installed, it is ignored.
        """
        self = clean_env(self)
        self.ensure_one()
        day = self.day_id
        day._check_driver()
        if self._is_resend(app_key):
            return self._app_result()
        if state not in APP_STOP_STATES:
            raise UserError(_("A stop can be marked done, absent or postponed."))
        if day.state != "in_progress":
            raise UserError(_("Start the day before marking its stops."))
        if self.state != "todo":
            raise UserError(_("This stop is already marked (%s). Ask the office to change it.",
                              dict(self._fields["state"]._description_selection(self.env))[self.state]))
        now = fields.Datetime.now()
        marked = parse_client_time(client_time, now, day.date_started) or now
        vals = {
            "state": state,
            "done_at": marked,
            "done_by": self.env.user.id,
            "sent_later": now - marked > OFFLINE_AFTER,
            "app_key": str(app_key)[:64] if app_key else False,
        }
        if note is not None:
            vals["note"] = str(note)[:NOTE_MAX] or False
        clean = self._clean_position(position)
        if clean and self.env["bf.route.notice.ack"]._may_read_position(self.env.user, day.company_id):
            vals.update(clean)
        self.sudo().with_context(bf_route_auto=True).write(vals)
        self._after_mark(state)
        return self._app_result()

    @api.model
    def _refusal_details(self, extra, stop):
        """What satellites can say about the data a refused mark carried (``stop`` may be
        empty: deleted by the office)."""
        return []

    def _app_result(self):
        """The stop as the phone shows it, and the position notice as it stands NOW: if the
        office turned positions off during the day, the phone stops reading them."""
        data = self._app_data(self._app_local())
        data["_notice"] = self.env["bf.route.notice.ack"]._status(self.day_id.company_id)
        return data

    def _after_mark(self, state):
        """A postponed stop moves to the next planned day of the same route, when there is one.
        Satellites hook here (a sale at the stop, for instance)."""
        self.ensure_one()
        if state != "postponed":
            return
        day = self.day_id.sudo()
        nxt = self.env["bf.route.day"].sudo().search(
            [("route_id", "=", day.route_id.id), ("date", ">", day.date), ("state", "=", "planned")],
            order="date", limit=1)
        if not nxt:
            return
        already = nxt.stop_ids.filtered(lambda s: s.partner_id == self.partner_id and s.state == "todo")[:1]
        if already:
            # Not a second stop for the same customer: what this one carried (an order of the
            # day, a changed quantity) is added to the one already planned.
            was_prepared = nxt._office_prepared()
            self._merge_postponed_into(already)
            # The day now holds more than the template: the template must not rewrite it.
            nxt.with_context(bf_route_auto=True).write({"edited": True})
            if was_prepared:
                # Its truck may already be loaded for less: the office checks.
                who = self.partner_id.display_name
                nxt._alert(lambda e: e._("Stop postponed into a prepared day: %s", nxt.name),
                           lambda e: e._("%(who)s was added to this day after the office prepared "
                                         "it: check the quantities and the truck.", who=who))
            nxt.message_post(body=_("Stop postponed from %(day)s: %(who)s, added to the stop "
                                    "already on this day.", day=day.date,
                                    who=self.partner_id.display_name))
            return
        last = max(nxt.stop_ids.mapped("sequence") or [0])
        self.sudo().with_context(bf_route_auto=True).create(
            dict(self._postponed_copy_vals(), day_id=nxt.id, sequence=last + 1))
        nxt.message_post(body=Markup("%s") % _("Stop postponed from %(day)s: %(who)s.",
                                               day=day.date, who=self.partner_id.display_name))

    def _merge_postponed_into(self, target):
        """Satellites carry what the postponed stop holds into ``target`` (products, for one)."""
        self.ensure_one()
        # Line by line: what the target already says is not said twice.
        known = (target.instructions or "").splitlines()
        new = [line for line in (self.instructions or "").splitlines() if line and line not in known]
        if new:
            target.sudo().with_context(bf_route_auto=True).instructions = "\n".join(known + new)

    def _postponed_copy_vals(self):
        """What a postponed stop brings to the next day. Satellites add to it."""
        self.ensure_one()
        return {
            "origin": "postponed",
            "partner_id": self.partner_id.id,
            "window_start": self.window_start,
            "window_end": self.window_end,
            "service_minutes": self.service_minutes,
            "instructions": self.instructions,
        }

    @api.model
    def _purge_positions(self):
        """Erase the positions older than each company's retention period."""
        # ⚠️ Archived companies too: their positions are just as personal.
        for company in self.env["res.company"].sudo().with_context(active_test=False).search([]):
            days = company.bf_route_position_days or 90
            limit = fields.Datetime.now() - timedelta(days=days)
            stale = self.sudo().with_context(active_test=False).search([
                ("company_id", "=", company.id), ("done_at", "<", limit), "|",
                ("latitude", "!=", 0), ("longitude", "!=", 0)])
            if stale:
                stale.with_context(bf_route_auto=True).write(
                    {"latitude": 0.0, "longitude": 0.0, "accuracy": 0.0, "position_erased": True})
