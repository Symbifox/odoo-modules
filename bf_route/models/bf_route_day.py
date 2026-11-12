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
Erased after the retention period by the watcher. The arrival at a stop reads none.

Arrival and departure: "I'm here" stamps the arrival (once, from the phone, sent later
without network like any mark); the mark that follows (done, absent, postponed) is the
departure. Their difference is the time on site. Marking without the arrival still works.
"""
import logging
import re
from datetime import timedelta

import pytz
from markupsafe import Markup, escape

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from .tools import haversine_m, local_to_utc, local_today, parse_client_time, utc_to_local
from .bf_route_engine import OSRM_PARAM

_logger = logging.getLogger(__name__)

# A mark made at the place. 150 m is a city block with its parking: under that, a gap
# says nothing at all. The accuracy the phone reported widens it when the phone itself
# says it was unsure. ⚠️ A reading aid, never a verdict: a large site, a service door at
# the back or a basement reading all land beyond it for honest reasons.
POSITION_NEAR_M = 150
# Proposing a position as a customer's place asks more than reading a gap: at 50 m a
# point is in the right building, at 200 m it can be the wrong side of the street.
POSITION_PLACE_MAX_ACCURACY_M = 50
# One request to the routing engine per day measured, in the person's own request.
MAX_TO_MEASURE = 200
# Positions erased in one pass, per company. The watcher comes back every fifteen minutes,
# so a backlog drains over a few passes instead of holding one long transaction: shortening
# a retention period from ten years to ninety days makes every old stop due at once.
PURGE_BATCH = 2000

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
    # The truck reaches the phone in sudo (_app_data): never one of another company.
    _check_company_auto = True

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
    vehicle_id = fields.Many2one("fleet.vehicle", string="Vehicle", tracking=True, check_company=True)
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
    # ⚠️ NOT summed into the mileage log's total, and that is deliberate: the log answers to
    # Revenu Québec, which asks for the odometer. This is a measurement of the road actually
    # driven between the positions read, printed beside the odometer so a day where the two
    # disagree says so, and so a day whose odometer nobody wrote down is not simply blank.
    distance_road_km = fields.Float(
        string="Road from the positions (km)", readonly=True, digits=(10, 1), copy=False,
        help="Road distance from the route's start point, through the positions read at the "
             "stops in the order they were marked, back to its end point, from the "
             "self-hosted OSRM. Needs at least two positions read. ⚠️ The two depot legs "
             "come from the route's own points, not from a reading: no position is read at "
             "the depot. Without them the figure covered only the marked stops and fell "
             "short of the odometer by the whole way out and back, which is most of a day. "
             "It is not the odometer and never replaces it.")
    duration_planned_minutes = fields.Integer(string="Planned driving time (min)", readonly=True,
                                              copy=False)
    visited = fields.Char(
        string="Customers visited", compute="_compute_visited", store=True,
        help="The customers driven to (stops done, or customer absent), in the order the "
             "office planned them: the purpose of the trip in the mileage log. ⚠️ Not the "
             "order they were marked in, which is what the measured road uses.")
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

    @api.depends("stop_ids.state", "stop_ids.partner_id", "stop_ids.sequence")
    def _compute_visited(self):
        for day in self:
            # An absent customer was driven to all the same: business kilometres.
            done = day.stop_ids.filtered(lambda s: s.state in ("done", "absent")).sorted("sequence")
            day.visited = ", ".join(dict.fromkeys(done.mapped("partner_id.display_name"))) or False

    @api.constrains("odometer_start", "odometer_end")
    def _check_odometer(self):
        """The office's corrections hold to the phone's bounds: the kilometres become money."""
        for day in self:
            for value in (day.odometer_start, day.odometer_end):
                if value and not 0 <= value <= MAX_ODOMETER:
                    raise ValidationError(_("The odometer must be between 0 and %s.", MAX_ODOMETER))
            if day.odometer_start and day.odometer_end:
                if day.odometer_end < day.odometer_start:
                    raise ValidationError(_("The odometer at the end is below the one at the start."))
                if day.odometer_end - day.odometer_start > MAX_KM_PER_DAY:
                    raise ValidationError(_("More than %s km in one day: check the odometer.",
                                            MAX_KM_PER_DAY))

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
        """The worker of the day, or a route manager. Anyone else is refused.

        ⚠️ The callers go on in sudo: the access rules are read here first. A manager sees
        every day, but only in their own companies (rule_day_company)."""
        if self.env.su:
            return
        self.check_access("read")
        if self._is_manager():
            return
        for day in self:
            if day.user_id != self.env.user:
                raise AccessError(_("This route day is not yours."))

    def _road_distance_from_positions(self):
        """Store the road distance through the positions read, when there are two of them.

        ⚠️ In the order the stops were MARKED (``done_at``), never the order the office
        planned (``sequence``). The two part ways as soon as the stops are taken in another
        order, which is the ordinary day on a road with a closed street, and this figure is
        printed in the kilometre register: measured on a three-stop day done out of order,
        the planned order gave 31 % more than the road actually driven. The field's help and
        the module's README both promise the marked order; this is where that promise is
        kept. A stop with no mark carries no position and takes no part in it.

        Best effort, and silent on failure: a day must be able to finish when the routing
        engine is down, and a missing measurement is not worth an error in the worker's
        face at six in the evening. Returns the days actually measured.
        """
        Engine = self.env["bf.route.engine"]
        measured = []
        for day in self.sudo():
            marked = day.stop_ids.filtered(
                lambda stop: stop.done_at and (stop.latitude or stop.longitude)
            ).sorted("done_at")
            points = [[stop.longitude, stop.latitude] for stop in marked]
            if len(points) < 2 or not Engine._url(OSRM_PARAM):
                continue
            # 🔴 The depot legs, or the figure is not comparable to the odometer and the
            # register prints a discrepancy on every honest day. No position is read at the
            # depot, so these two points come from the route itself. Measured on a test base:
            # a three-stop day was 31.0 km between its marked stops and 84.6 km with the way
            # out and back, so the column showed the odometer 173 % too high, every time,
            # under a note inviting the office to compare them.
            route = day.route_id
            start = self._depot_point(route.start_partner_id or route.company_id.partner_id)
            end = self._depot_point(route.end_partner_id or route.start_partner_id
                                    or route.company_id.partner_id)
            if start:
                points.insert(0, start)
            if end:
                points.append(end)
            try:
                road = Engine._route(points)
            except UserError:
                continue
            day.with_context(bf_route_auto=True).distance_road_km = round(
                road["distance"] / 1000.0, 1)
            measured.append(day.id)
        return self.browse(measured)

    @api.model
    def _depot_point(self, partner):
        """[lon, lat] of a depot, its own coordinates or its street point, else None.

        Same order of preference as ``app_map``'s ``place()``, and the same convention: an
        unset Odoo Float reads 0.0, so a depot nobody located is simply absent rather than
        a point in the Gulf of Guinea.
        """
        partner = partner.sudo()
        if partner.partner_latitude or partner.partner_longitude:
            return [partner.partner_longitude, partner.partner_latitude]
        if partner.bf_route_street_latitude or partner.bf_route_street_longitude:
            return [partner.bf_route_street_longitude, partner.bf_route_street_latitude]
        return None

    def action_road_distance(self):
        """Recompute the road distance from the positions. Route manager only: it reads them.

        ⚠️ Public method, so reachable over RPC with any id: the access rules are read
        first, before the measurement goes on in sudo. Without it, a manager of one company
        could measure a day of another.
        """
        self.check_access("read")
        if not self._is_manager():
            raise AccessError(_("Only a route manager measures the road from the positions."))
        if len(self) > MAX_TO_MEASURE:
            raise UserError(_("Measure at most %s days at a time: each one is a request to "
                              "the routing engine.", MAX_TO_MEASURE))
        measured = self._road_distance_from_positions()
        # ⚠️ A button that measured nothing says so. The automatic path at the end of the
        # day stays silent on purpose, but a person who clicked and saw the figure stay the
        # same would read it as the measurement confirming itself.
        if len(self) == 1 and not measured:
            raise UserError(_(
                "Nothing to measure on this day: it takes two stops marked with a position. "
                "They may have been erased at the end of the retention period, or the "
                "routing engine may be unreachable. The figure already shown, if any, was "
                "left exactly as it was."))
        return True

    def _check_on_the_road(self, not_started):
        """The day is on the road, or the phone is told why not: ``not_started`` for a planned
        day; a day the office finished or cancelled says so (starting it is not the answer)."""
        self.ensure_one()
        if self.state == "done":
            raise UserError(_("This day is finished: nothing more can be recorded on it. "
                              "Call the office."))
        if self.state == "canceled":
            raise UserError(_("This day was cancelled: nothing can be recorded on it. "
                              "Call the office."))
        if self.state != "in_progress":
            raise UserError(not_started)

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
            day._road_distance_from_positions()
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
        # What the phone carried, in words: the office reads "Arrival", not the code "arrived".
        labels = dict(Stop._fields["state"]._description_selection(self.env), arrived=_("Arrival"))
        state = str(state or "")[:20]
        carried = [labels.get(state, state), str(note or "")[:NOTE_MAX]] + Stop._refusal_details(extra, stop)
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

    def app_map(self):
        """What the map of the day draws: the start, the stops in order, the return point and
        the road between them (OSRM), with the tile server to draw them on.

        The planned points are customers' places only: their coordinates, or else the point
        of their street. A place without coordinates is listed with none and left off the
        map; nothing is geocoded here. Without OSRM, or when it fails, the stops are joined
        by straight lines on the phone.

        ⚠️ The positions READ at the stops travel in their own list, under "read", and
        never as planned points: on a screen the two must not be confused, one being where
        the customer is and the other where a person was at a moment. ``_read_positions``
        decides who sees which of them.
        """
        self = clean_env(self)
        self.ensure_one()
        self._check_driver()
        day = self.sudo()
        route = day.route_id
        start = route.start_partner_id or route.company_id.partner_id
        end = route.end_partner_id or start
        Engine = self.env["bf.route.engine"]

        def place(partner):
            if partner.partner_latitude or partner.partner_longitude:
                return partner.partner_latitude, partner.partner_longitude
            if partner.bf_route_street_latitude or partner.bf_route_street_longitude:
                return partner.bf_route_street_latitude, partner.bf_route_street_longitude
            # False, not None: XML-RPC cannot send None.
            return False, False

        points = []

        def add(kind, partner, **extra):
            lat, lon = place(partner)
            points.append(dict(extra, kind=kind, label=partner.display_name or "", lat=lat, lon=lon))

        add("depot" if start == end else "start", start)
        for rank, stop in enumerate(day.stop_ids.sorted("sequence"), 1):
            add("stop", stop.partner_id, rank=rank, state=stop.state, stop_id=stop.id)
        if end != start:
            add("end", end)
        located = [[p["lon"], p["lat"]] for p in points if p["lat"] is not False]
        if end == start and points[0]["lat"] is not False:
            located.append([points[0]["lon"], points[0]["lat"]])
        line = False
        if len(located) >= 2 and Engine._url(OSRM_PARAM):
            try:
                line = Engine._route_line(located)
            except UserError:
                line = False
        return {"points": points, "line": line, "tiles": Engine._tiles(),
                "read": day._read_positions()}

    def _read_positions(self):
        """The positions read at the stops of this day, for the map.

        ⚠️ Here, and only here, the coordinates do leave for a browser: a map of where the
        marks were made is the whole point of the feature, and it is shown to the route
        manager and to the person on their own readings (s. 27 of Law 25). ``app_map`` has
        already run ``_check_driver``, so nobody else reaches this.

        ⚠️ A manager sees every reading of the day; anyone else sees only the ones they
        took themselves, which ``done_by`` holds. The day's ``user_id`` is NOT the test: the
        office reassigns a finished day (a worker who leaves, a correction), and the new
        holder would then be handed the raw coordinates of the person who really drove it.
        Not ``env.su`` either, because ``sudo()`` keeps the uid and ``app_map`` calls this on
        a sudo recordset precisely so the customers' names can be read.

        Erased positions are left out rather than drawn at (0, 0), which is a point in the
        Gulf of Guinea and would read as a mark made at sea.
        """
        self.ensure_one()
        everything = self._is_manager()
        me = self.env.user
        kept = self.company_id.bf_route_position_days or 90
        out = []
        for rank, stop in enumerate(self.sudo().stop_ids.sorted("sequence"), 1):
            if not (stop.latitude or stop.longitude):
                continue
            if not everything and stop.done_by != me:
                continue
            accuracy = round(stop.accuracy) if stop.accuracy else 0
            out.append({
                "stop_id": stop.id,
                "rank": rank,
                "label": stop.partner_id.display_name or "",
                # ⚠️ The whole phrase is built here, not assembled in the template: a
                # tooltip stitched together from numbers printed "read at 0 m (± 0 m)" for a
                # customer who has no place to compare with, and nothing can be translated
                # from fragments. ``mine`` chooses the voice: the manager is reading someone
                # else's day, and "your position" would be false on their screen.
                "note": stop._position_sentence(
                    stop.position_check or "none", stop.position_gap_m, accuracy, kept,
                    mine=stop.done_by == me),
                "lat": stop.latitude,
                "lon": stop.longitude,
                "accuracy": accuracy,
                "check": stop.position_check or "none",
            })
        return out


class BfRouteDayStop(models.Model):
    _name = "bf.route.day.stop"
    _description = "Stop of a route day"
    _order = "day_id, sequence, id"
    # ⚠️ The customer's name, phone, address and place reach the phone in sudo (app_map,
    # app_arrive, app_mark): never a contact of another company.
    _check_company_auto = True

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
    partner_id = fields.Many2one("res.partner", string="Customer", required=True, check_company=True)
    address = fields.Char(related="partner_id.contact_address", string="Address")
    window_start = fields.Float(string="Not before")
    window_end = fields.Float(string="Not after")
    service_minutes = fields.Integer(string="Time on site (min)", default=10)
    instructions = fields.Text(string="Access instructions")
    planned_time = fields.Datetime(string="Planned at", copy=False)
    planned_clock = fields.Char(string="Planned time", compute="_compute_clocks",
                                help="Local time, in the route's time zone.")
    state = fields.Selection(STOP_STATES, default="todo", required=True, index=True, copy=False)
    arrived_at = fields.Datetime(string="Arrived at", readonly=True, copy=False)
    arrived_clock = fields.Char(string="Arrived", compute="_compute_clocks",
                                help="Local time, in the route's time zone.")
    # Stored: the Visits list adds it up and groups it.
    on_site_minutes = fields.Integer(string="On site (min)", compute="_compute_on_site", store=True,
                                     help="From the arrival to the mark that closed the stop.")
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
    # The gap, not the coordinates: it answers the only question anyone asks of a position,
    # and it is what the screens show. Stored, so the stops list can sort and group on it.
    # 🔴 The same ``groups`` as the coordinates, and for a reason measured on a test base:
    # a stored computed field without it is readable, SEARCHABLE and GROUPABLE by anyone the
    # record rules let near the record, and those rules key on the day's holder
    # (``day_id.user_id``), not on who made the mark. The office reassigns a finished day,
    # and the new holder read "Away from the place, 2,043 m" on a stop they never marked,
    # plus the margin, straight off the standard Route days screen. The right of access of
    # s. 27 is served where it belongs, to the person who MADE the mark, by
    # ``_app_position`` on their phone, which filters on ``done_by``.
    position_gap_m = fields.Integer(
        string="Gap to the place (m)", compute="_compute_position_gap", store=True,
        groups="bf_route.group_route_manager",
        help="Distance as the crow flies between the position read when the stop was marked "
             "and the customer's place. It reads 0 whenever there is nothing to compare; the "
             "state beside it says which case that is. It is erased with the position. The "
             "person who marked the stop reads it on their phone, in words.")
    position_check = fields.Selection(
        [("none", "No position"),
         ("unplaced", "Customer without a place"),
         ("near", "At the place"),
         ("vague", "Within the phone's margin"),
         ("far", "Away from the place"),
         ("from_reading", "Place itself from a reading"),
         ("erased", "Position erased")],
        string="Position", compute="_compute_position_gap", store=True,
        groups="bf_route.group_route_manager",
        help="What the gap means. \"Away from the place\" is a reading, not a verdict: a "
             "large site, a service door at the back, or a phone that reported a poor "
             "accuracy all show up this way. \"Within the phone's margin\" is the honest "
             "answer when the reading's own margin straddles the block: such a reading "
             "proves nothing either way. \"Place itself from a reading\" means the place "
             "being compared with was taken from a position read on a route, so the gap "
             "compares a reading with a reading and confirms nothing at all.")

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
            # The postal code and the province: a common street name exists in many towns, and
            # this is what the directions links send when the stop has no coordinates.
            "address": ", ".join(p for p in (partner.street, partner.street2, partner.city,
                                             " ".join(q for q in (partner.state_id.code, partner.zip) if q))
                                 if p),
            "phone": partner.mobile or partner.phone or False,
            "latitude": partner.partner_latitude or False,
            "longitude": partner.partner_longitude or False,
            "window": self._window_label(),
            "planned_time": local(self.planned_time),
            "instructions": self.instructions or False,
            "note": self.note or False,
            "done_at": local(self.done_at),
            "arrived_at": local(self.arrived_at),
            "position": self._app_position(),
        }

    def action_propose_place(self):
        """Write the position read at this stop as the customer's place on the street.

        The second purpose declared in the privacy register (`route_place_from_position`):
        a position collected to prove a visit, used to locate the customer visited. Québec's
        Law 25 allows a use for a compatible purpose (s. 12), and this one is only compatible
        while it stays honest, so three refusals rather than a silent write:

        * a customer who already has a place is never moved by it, and that means EITHER
          place: their own coordinates, or the street point the geocoder or an earlier
          reading left. ``position_check == "unplaced"`` is the single test, the same one
          the button's own condition uses, so what the screen offers and what the method
          accepts cannot drift apart;
        * a reading whose accuracy the phone did not report, or reported above the ceiling
          of ``POSITION_PLACE_MAX_ACCURACY_M``, is refused: at 200 m a point lands across
          the street;
        * it is a button, never a cron. A person accepts the proposal, the contact's chatter
          says where its coordinates came from, and ``bf_route_street_from_position`` stamps
          the date on the contact, because a gap measured against a place that came from a
          reading is no longer an independent check of anything.

        ⚠️ Public method, so reachable over RPC with any id. The access rules are read
        FIRST, before the loop goes on in sudo: a group check alone lets a route manager of
        one company pass ids of another, and the stops of several companies share their
        contacts. Measured on a test base: without this line, a route manager read back
        a worker's phone position from another company through a shared contact.
        """
        if not (self.env.su or self.env.user.has_group("bf_route.group_route_manager")):
            raise AccessError(_("Only a route manager can do this."))
        self.check_access("read")
        for stop in self.sudo():
            partner = stop.partner_id
            check = stop.position_check
            if check in (False, "none"):
                raise UserError(_("No position was read at the stop of %s.",
                                  partner.display_name))
            if check == "erased":
                raise UserError(_(
                    "The position read at the stop of %s was erased at the end of the "
                    "retention period: there is nothing left to propose.",
                    partner.display_name))
            if check != "unplaced":
                raise UserError(_(
                    "%s already has a place on the map, their own coordinates or a street "
                    "point. Clear it first if it is the wrong one: a position read at the "
                    "door is not a reason to move a contact someone placed on purpose.",
                    partner.display_name))
            if not stop.accuracy:
                raise UserError(_(
                    "The phone did not say how accurate this reading was, so it cannot be "
                    "turned into the place of %s.", partner.display_name))
            if stop.accuracy > POSITION_PLACE_MAX_ACCURACY_M:
                raise UserError(_(
                    "This reading is accurate to %(accuracy)s m, beyond the %(max)s m a "
                    "place is proposed from: it could be the wrong side of the street.",
                    accuracy=round(stop.accuracy), max=POSITION_PLACE_MAX_ACCURACY_M))
            partner.write({"bf_route_street_latitude": stop.latitude,
                           "bf_route_street_longitude": stop.longitude,
                           "bf_route_street_from_position": fields.Datetime.now()})
            # ⚠️ The margin the phone reported is NOT repeated here. A contact is readable
            # by every internal user and, when it carries no company, by all of them across
            # companies: what belongs on it is where its coordinates came from, not how well
            # someone's phone was performing that morning. The stop keeps that, behind the
            # route manager's group.
            partner.message_post(body=Markup("<p>%s</p>") % _(
                "Place on the map taken from a position read at the stop of %(date)s on the "
                "route %(route)s, accepted by %(who)s. It plans the kilometres; it does not "
                "guide the phone, and a gap measured against it is not a check of the "
                "reading it came from.",
                date=stop.day_id.date, route=stop.day_id.route_id.display_name or "-",
                who=self.env.user.display_name))
        return True

    def _app_position(self):
        """What the person is told about the position read on THEIR stop (s. 27 of Law 25).

        ⚠️ The gap and the accuracy, never the coordinates. The phone has no use for them,
        and a payload that carries them puts the position back in the browser's cache, in
        the proxy's log and in every trace on the way, for nothing.

        ⚠️ Only to the person who MADE the mark (``done_by``), or to a route manager. The
        day's holder is not the test: the office reassigns a finished day, and the screen
        would then tell the new holder "your position was read 80 m from the place" about a
        reading taken by someone who no longer works here. The right of access of s. 27 is a
        right over one's OWN personal information, so a stop marked by someone else simply
        says nothing here.
        """
        self.ensure_one()
        private = self.sudo()
        if private.position_check in (False, "none"):
            return False
        mine = private.done_by == self.env.user
        if not (mine or self.day_id._is_manager()):
            return False
        gap = private.position_gap_m
        accuracy = round(private.accuracy) if private.accuracy else 0
        kept = private.day_id.company_id.bf_route_position_days or 90
        return {
            "state": private.position_check,
            "gap_m": gap,
            "accuracy": accuracy,
            "kept_days": kept,
            "label": self._position_sentence(private.position_check, gap, accuracy, kept,
                                             mine=mine),
        }

    @api.model
    def _position_sentence(self, check, gap, accuracy, kept, mine=True):
        """One whole sentence per case, built here and not in the template.

        ⚠️ A template that assembles the sentence around ``<t t-esc>`` elements exports as
        fragments: "days", "m)", "· kept". Nobody can translate those, and no French word
        order can follow them. Each piece below is a sentence of its own, so joining the
        translated pieces stays correct in any language.

        ``mine`` is the voice. The person reads about their own reading ("your position");
        a route manager reads about someone else's day, where "your position" would simply
        be false, and the retention period is the person's business, not a line on a map.

        ⚠️ A reading whose margin the phone never reported says so out loud. Silence
        there reads as a precise measurement, which is exactly what it is not.
        """
        if check == "erased":
            return _("The position read here has been erased.")
        if check in (False, "none"):
            # 🔴 No sentence used to exist for this state: it fell into the "far" default
            # below and printed "Position read 0 m from the customer's place." about a stop
            # that carries no reading at all. Unreachable from the two callers today, which
            # is why nobody saw it, and one line away from being read by someone.
            return ""
        if check == "from_reading":
            return _("Your position was read %s m from this customer's place, which was "
                     "itself taken from a position read on a route: the two compare a "
                     "reading with a reading and confirm nothing.", self._metres(gap)) \
                if mine else \
                _("Position read %s m from this customer's place, which was itself taken "
                  "from a position read on a route: the two compare a reading with a "
                  "reading and confirm nothing.", self._metres(gap))
        if check == "unplaced":
            parts = [_("Your position was read here; this customer has no place on the map yet.")
                     if mine else
                     _("Position read at this stop; this customer has no place on the map yet.")]
        elif check == "near" and not gap:
            # "Read 0 m from the place" is true and unreadable, which is what the near case
            # exists to say plainly. The metres are left out rather than printed as zero.
            parts = [_("Your position was read at the customer's place.") if mine else
                     _("Position read at the customer's place.")]
        elif check == "near":
            parts = [_("Your position was read at the customer's place, %s m away.",
                       self._metres(gap))
                     if mine else
                     _("Position read at the customer's place, %s m away.", self._metres(gap))]
        elif check == "vague":
            parts = [_("Your position was read %(gap)s m from the customer's place, and your "
                       "phone reported a margin of %(accuracy)s m: it says neither that you "
                       "were there nor that you were not.",
                       gap=self._metres(gap), accuracy=self._metres(accuracy))
                     if mine else
                     _("Position read %(gap)s m from the customer's place, with a margin of "
                       "%(accuracy)s m reported by the phone: it says neither that the "
                       "person was there nor that they were not.",
                       gap=self._metres(gap), accuracy=self._metres(accuracy))]
        else:
            parts = [_("Your position was read %s m from the customer's place.",
                       self._metres(gap))
                     if mine else
                     _("Position read %s m from the customer's place.", self._metres(gap))]
        if accuracy and check != "vague":
            parts.append(_("To within %s m.", self._metres(accuracy)))
        elif not accuracy:
            parts.append(_("Your phone did not report how accurate it was.") if mine else
                         _("The phone did not report how accurate it was."))
        if mine:
            parts.append(_("Kept %s.",
                           self.env["bf.route.notice.ack"]._retention_text(kept)))
        return " ".join(parts)

    @api.model
    def _metres(self, value):
        """A number of metres, grouped the way the reader's language groups them.

        ⚠️ "4000 m" beside "de 1 à 3 650 jours" is two typographic conventions in one
        module, and the four-digit case arrives as soon as a phone reads badly indoors.
        Falls back to the bare number rather than failing a sentence.
        """
        try:
            lang = self.env["res.lang"]._lang_get(self.env.lang or "en_US")
            return lang.format("%d", int(value or 0), grouping=True).strip()
        except Exception:  # noqa: BLE001 - a label is never worth an exception
            return str(int(value or 0))

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

    @api.depends("planned_time", "done_at", "arrived_at", "day_id.route_id")
    def _compute_clocks(self):
        """The time alone, as the day's list shows it: the date is the day's."""
        for stop in self:
            route = stop.day_id.route_id.sudo()
            tz = route._tz() if route else pytz.utc
            stop.planned_clock = utc_to_local(tz, stop.planned_time).strftime("%H:%M") \
                if stop.planned_time else False
            stop.done_clock = utc_to_local(tz, stop.done_at).strftime("%H:%M") \
                if stop.done_at else False
            stop.arrived_clock = utc_to_local(tz, stop.arrived_at).strftime("%H:%M") \
                if stop.arrived_at else False

    @api.depends("latitude", "longitude", "accuracy", "position_erased",
                 "partner_id.partner_latitude", "partner_id.partner_longitude",
                 "partner_id.bf_route_street_latitude", "partner_id.bf_route_street_longitude",
                 "partner_id.bf_route_street_from_position")
    def _compute_position_gap(self):
        """The gap between the position read and the customer's place, and what it means.

        ⚠️ The position is read in sudo. The three fields are restricted to the route
        manager, and this compute is triggered by writes anyone may make, a customer's
        address being the obvious one: reading them with the writer's own rights would
        raise for a worker who corrects a phone number.

        ⚠️ The gap dies with the position. The purge sets the coordinates back to zero,
        this recomputes, and what is left is the state "erased": keeping a gap after the
        retention period would keep a coarser copy of what was just destroyed.
        """
        # One sudo recordset for the lot, not one per stop: an upgrade recomputes every
        # stop of the base, and a client with ten thousand of them would pay an environment
        # switch each time. Same ids, same order, so the pairing holds.
        for stop, private in zip(self, self.sudo()):
            partner = private.partner_id
            door = bool(partner.partner_latitude or partner.partner_longitude)
            if door:
                place = (partner.partner_latitude, partner.partner_longitude)
            else:
                place = (partner.bf_route_street_latitude, partner.bf_route_street_longitude)
            # 🔴 The place being compared with may itself have come from a reading, and
            # then the gap is not a check of anything: both points were read by the same kind
            # of phone at the same door. Measured on a test base: one click of the office on the
            # stop of 12 October turned that stop green at 0 m AND turned the stop of
            # 19 October, which nobody touched and someone else may have marked, orange at
            # 178 m. The verdict spread to every stop of that customer, past and future, and
            # outlived the erasure of the reading it was measured against. The door wins when
            # there is one: a contact the geocoder later placed properly is an independent
            # reference again, whatever the stale street point says.
            from_reading = not door and bool(partner.bf_route_street_from_position)
            # ⚠️ Presence is tested HERE, on both points, and never left to a sentinel
            # inside ``haversine_m``: an unset Odoo Float reads 0.0, and 0.0 is also a real
            # coordinate. Same convention as ``app_map``'s ``place()``.
            read = bool(private.latitude or private.longitude)
            placed = bool(place[0] or place[1])
            gap = haversine_m(private.latitude, private.longitude, place[0], place[1]) \
                if read and placed else None
            if gap is None:
                stop.position_gap_m = 0
                if read:
                    stop.position_check = "unplaced"
                else:
                    stop.position_check = "erased" if private.position_erased else "none"
                continue
            stop.position_gap_m = round(gap)
            if from_reading:
                stop.position_check = "from_reading"
                continue
            # 🔴 The reading is a DISC, not a point: its centre is ``gap`` from the place
            # and its radius is the margin the phone reported. So the three answers are about
            # that disc against the 150 m block, and they are symmetric:
            #   the whole disc inside the block  -> "near", presence is the only reading;
            #   the whole disc outside           -> "far",  absence is the only reading;
            #   the disc straddles the edge      -> "vague", it proves nothing either way.
            # The earlier form compared the gap alone to the threshold and only then to the
            # margin, which was lax in one direction and strict in the other: measured on the
            # bench, a reading AT the door with a 400 m margin came out green "At the place",
            # while a reading 151 m away with a 150 m margin, far more precise, came out
            # orange "Away from the place". The most precise reading got the worst verdict,
            # and the green one was a claim the data could not carry. A green badge is a
            # claim in the person's favour and an orange one a claim against them: both have
            # to be earned.
            margin = private.accuracy or 0.0
            if gap + margin <= POSITION_NEAR_M:
                stop.position_check = "near"
            elif gap - margin > POSITION_NEAR_M:
                stop.position_check = "far"
            else:
                stop.position_check = "vague"

    @api.depends("arrived_at", "done_at", "state")
    def _compute_on_site(self):
        """⚠️ Not for a stop the office put back to "to do": its old mark stays in ``done_at``
        but closes nothing, and the Visits list would add it up."""
        for stop in self:
            if stop.state != "todo" and stop.arrived_at and stop.done_at and stop.done_at >= stop.arrived_at:
                stop.on_site_minutes = round((stop.done_at - stop.arrived_at).total_seconds() / 60)
            else:
                stop.on_site_minutes = 0

    def _is_resend(self, app_key):
        self.ensure_one()
        return bool(app_key) and self.app_key == str(app_key)[:64]

    def app_arrive(self, client_time=None, app_key=None):
        """The worker is at the stop ("I'm here"). Once per stop: a second arrival, or the same
        one sent twice by the phone, keeps the first time. No position is read at the arrival.
        ``app_key`` is accepted for the phone's queue and not stored."""
        self = clean_env(self)
        self.ensure_one()
        day = self.day_id
        day._check_driver()
        if self.arrived_at:
            return self._app_result()
        day._check_on_the_road(_("Start the day before arriving at its stops."))
        if self.state != "todo":
            raise UserError(_("This stop is already marked (%s). Ask the office to change it.",
                              dict(self._fields["state"]._description_selection(self.env))[self.state]))
        now = fields.Datetime.now()
        # The phone's clock may run a few minutes ahead: an arrival is never in the future
        # (the departure is kept at or after it).
        arrived = min(parse_client_time(client_time, now, day.date_started) or now, now)
        self.sudo().with_context(bf_route_auto=True).write({"arrived_at": arrived})
        return self._app_result()

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
        day._check_on_the_road(_("Start the day before marking its stops."))
        if self.state != "todo":
            raise UserError(_("This stop is already marked (%s). Ask the office to change it.",
                              dict(self._fields["state"]._description_selection(self.env))[self.state]))
        now = fields.Datetime.now()
        marked = parse_client_time(client_time, now, day.date_started) or now
        # The departure is never before the arrival: the time on site cannot be negative.
        if self.arrived_at and marked < self.arrived_at:
            marked = self.arrived_at
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
        """Erase the positions older than each company's retention period.

        ⚠️ One savepoint PER COMPANY. ``_cron_watch`` already isolates each job, but that
        is one savepoint for the whole sweep: a single company that raises undid the
        erasure of all the others, including the ones already written and verified. And the
        companion register does raise on purpose, when it cannot verify that a position
        really left the table. Without this, one unverifiable stop in one company would
        keep the positions of the four others for another fifteen minutes, every fifteen
        minutes, silently.

        ⚠️ A rollback does not empty the ORM cache, so the cache is dropped with it.
        Otherwise the next company would be read through records that no longer exist in
        the transaction.
        """
        # ⚠️ Archived companies too: their positions are just as personal.
        for company in self.env["res.company"].sudo().with_context(active_test=False).search([]):
            try:
                with self.env.cr.savepoint():
                    self._purge_positions_of(company)
            except Exception:
                self.env.invalidate_all()
                _logger.exception(
                    "bf_route: the positions of %s were not erased on this pass; the other "
                    "companies were", company.display_name)

    def _purge_positions_of(self, company):
        """Erase one company's expired positions, inside its own savepoint.

        At most ``PURGE_BATCH`` stops per pass: shortening a retention period makes years
        of stops due at once, and the watcher comes back every fifteen minutes.
        """
        days = company.bf_route_position_days or 90
        limit = fields.Datetime.now() - timedelta(days=days)
        stale = self.sudo().with_context(active_test=False).search([
            ("company_id", "=", company.id), ("done_at", "<", limit), "|",
            ("latitude", "!=", 0), ("longitude", "!=", 0)], limit=PURGE_BATCH)
        if not stale:
            return
        stale.with_context(bf_route_auto=True).write(
            {"latitude": 0.0, "longitude": 0.0, "accuracy": 0.0, "position_erased": True})
        stale._bf_route_positions_erased(company, days)

    def _bf_route_positions_erased(self, company, days):
        """Extension point: the positions of ``self`` have just been erased.

        Called once per company, with the stops whose coordinates were set back to zero and
        the retention period that expired. It does nothing here on purpose: this module
        erases, it does not keep a register. The companion `bf_route_privacy` writes one
        entry per stop to the privacy register of Law 25, in partial scope.

        ⚠️ Called AFTER the write, so the fields are already zero. That is what lets the
        companion verify the erasure really happened before attesting it, which is the
        whole point of calling it here rather than before.
        """
        return True
