"""The route template, its stops, and the watcher that creates days and raises alerts.

⚠️ One alert per problem, never one per run of the watcher: it runs every
quarter of an hour, and an activity that comes back at each run becomes noise
everybody learns to ignore (same rule as bf_nfc_round).
"""
import logging
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from .tools import local_to_utc, local_today, tz_of

_logger = logging.getLogger(__name__)

WEEKDAY_FIELDS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
# Fields that decide on which dates the route runs: changing one removes the planned days
# that no longer fit (unless edited by hand) and creates the new ones.
CALENDAR_FIELDS = set(WEEKDAY_FIELDS) | {"recurrence", "interval_weeks", "date_start", "date_end",
                                         "active"}
# Fields of a route that a planned day copies: changing one refreshes the days to come.
DAY_SOURCE_FIELDS = {"stop_ids", "user_id", "vehicle_id", "start_hour",
                     "start_partner_id", "end_partner_id"}


class BfRoute(models.Model):
    _name = "bf.route"
    _description = "Work route"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "name"

    name = fields.Char(string="Route", required=True, tracking=True)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one("res.company", default=lambda self: self.env.company, required=True)
    responsible_id = fields.Many2one(
        "res.users", string="Responsible", required=True, default=lambda self: self.env.user,
        tracking=True, help="Receives the alerts. The route runs in this person's time zone.")
    user_id = fields.Many2one(
        "res.users", string="Worker", tracking=True,
        help="Who drives the route by default. Each day can be given to someone else.")
    vehicle_id = fields.Many2one("fleet.vehicle", string="Vehicle", tracking=True)
    recurrence = fields.Selection(
        [("none", "On demand"), ("weekly", "Weekly")], string="Runs", default="weekly",
        required=True, tracking=True)
    mon = fields.Boolean(string="Monday", default=True)
    tue = fields.Boolean(string="Tuesday")
    wed = fields.Boolean(string="Wednesday")
    thu = fields.Boolean(string="Thursday")
    fri = fields.Boolean(string="Friday")
    sat = fields.Boolean(string="Saturday")
    sun = fields.Boolean(string="Sunday")
    interval_weeks = fields.Integer(
        string="Every (weeks)", default=1,
        help="1 = every week, 2 = every other week, counted from the start date.")
    date_start = fields.Date(string="From", default=fields.Date.context_today, required=True)
    date_end = fields.Date(string="Until")
    start_hour = fields.Float(string="Leaves at", default=8.0,
                              help="Local time of the person responsible.")
    start_partner_id = fields.Many2one(
        "res.partner", string="Leaves from", default=lambda self: self.env.company.partner_id,
        help="The depot. Its coordinates are used to plan the distance and the order.")
    end_partner_id = fields.Many2one(
        "res.partner", string="Returns to", default=lambda self: self.env.company.partner_id)
    days_ahead = fields.Integer(string="Days created ahead", default=7)
    stop_ids = fields.One2many("bf.route.stop", "route_id", string="Stops", copy=True)
    stop_count = fields.Integer(compute="_compute_counts")
    day_ids = fields.One2many("bf.route.day", "route_id", string="Route days")
    day_count = fields.Integer(compute="_compute_counts")
    distance_km = fields.Float(string="Planned distance (km)", readonly=True, digits=(10, 1))
    duration_minutes = fields.Integer(string="Planned driving time (min)", readonly=True)

    _sql_constraints = [
        ("interval_positive", "CHECK(interval_weeks >= 1)", "A route runs every week at most."),
        ("days_ahead_range", "CHECK(days_ahead >= 0 AND days_ahead <= 60)",
         "Days are created from 0 to 60 days ahead."),
    ]

    def _message_auto_subscribe_followers(self, updated_values, default_subtype_ids):
        """⚠️ No "you have been assigned" message and no follower for the worker. Odoo sends it
        for any ``user_id``: one per day the watcher creates, for every worker, who follows
        the days on the phone anyway. And Odoo refuses to send it when the person assigning
        has no email address, which would block the creation itself."""
        return []

    @api.depends("stop_ids", "day_ids")
    def _compute_counts(self):
        for route in self:
            route.stop_count = len(route.stop_ids)
            route.day_count = len(route.day_ids)

    @api.constrains("date_start", "date_end")
    def _check_dates(self):
        for route in self:
            if route.date_end and route.date_end < route.date_start:
                raise ValidationError(_("A route cannot end before it starts."))

    def _tz(self):
        self.ensure_one()
        return tz_of(self.responsible_id, self.company_id.partner_id)

    @api.model
    def _tz_for(self, user):
        return tz_of(user, self.env.company.partner_id)

    def _check_manager(self):
        if not (self.env.su or self.env.user.has_group("bf_route.group_route_manager")):
            raise AccessError(_("Only a route manager can do this."))

    def _runs_on(self, day):
        """True when this weekly route has a day on ``day`` (a local date)."""
        self.ensure_one()
        if self.recurrence != "weekly" or not self.active:
            return False
        if day < self.date_start or (self.date_end and day > self.date_end):
            return False
        if not self[WEEKDAY_FIELDS[day.weekday()]]:
            return False
        anchor = self.date_start - timedelta(days=self.date_start.weekday())
        weeks = (day - anchor).days // 7
        return weeks % max(self.interval_weeks, 1) == 0

    # ------------------------------------------------------------------
    # Days
    # ------------------------------------------------------------------
    def _stop_vals_for_day(self):
        self.ensure_one()
        return [(0, 0, stop._day_stop_vals()) for stop in self.stop_ids]

    def _day_vals(self, day):
        self.ensure_one()
        return {
            "route_id": self.id,
            "date": day,
            "recurrence_date": day,
            "user_id": self.user_id.id,
            "vehicle_id": self.vehicle_id.id,
            "date_planned_start": local_to_utc(self._tz(), day, self.start_hour),
            "stop_ids": self._stop_vals_for_day(),
        }

    def _generate_days(self, today=None):
        """Create the missing days of the coming ``days_ahead`` days. Never twice the same day:
        a cancelled day stays, so it is not created again."""
        Day = self.env["bf.route.day"].sudo()
        created = Day
        for route in self.filtered(lambda r: r.recurrence == "weekly" and r.active):
            start = today or local_today(route._tz())
            wanted = [start + timedelta(days=n) for n in range(route.days_ahead + 1)]
            wanted = [d for d in wanted if route._runs_on(d)]
            if not wanted:
                continue
            # A day moved by hand to another date still answers for the date it was made for.
            found = Day.with_context(active_test=False).search(
                [("route_id", "=", route.id), "|", ("date", "in", wanted),
                 ("recurrence_date", "in", wanted)])
            existing = set(found.mapped("date")) | set(found.mapped("recurrence_date"))
            for day in wanted:
                if day not in existing:
                    created |= Day.with_context(bf_route_auto=True).create(route._day_vals(day))
        return created

    def _drop_obsolete_days(self):
        """Planned days the calendar no longer wants (other weekdays, past the end date, route
        archived or on demand) are removed, unless the office edited them: those stay, with a
        note saying why."""
        for route in self:
            today = local_today(route._tz())
            days = route.day_ids.filtered(lambda d: d.state == "planned" and d.date >= today
                                          and d.recurrence_date)
            obsolete = days.filtered(lambda d: not route._runs_on(d.recurrence_date))
            kept = obsolete.filtered(lambda d: d._office_prepared() or d._keep_when_dropped())
            (obsolete - kept).with_context(bf_route_auto=True).unlink()
            for day in kept:
                day.sudo().message_post(body=_("The route no longer runs on this date. This day was edited "
                                        "by hand and was left as it is: cancel it if it is not needed."))

    def _refresh_upcoming_days(self):
        """Copy the template again into the planned days that have not started.

        Stops added by hand to one day stay; stops that came from the template are
        replaced, unless one was already marked (it is kept as it is).
        """
        for route in self.with_context(bf_route_auto=True):
            today = local_today(route._tz())
            days = route.day_ids.filtered(lambda d: d.state == "planned" and d.date >= today)
            # ⚠️ A day the office edited (another worker, another truck, other quantities) is
            # never overwritten: it gets a note, the office decides.
            prepared = days.filtered(lambda d: d._office_prepared())
            for day in prepared:
                day.sudo().message_post(body=_("The route changed. This day was edited by hand and was "
                                        "left as it is."))
            for day in days - prepared:
                # By ORIGIN, not by the link: a stop removed from the template loses its link
                # (set null) but is still a copy of the template, to be replaced.
                from_template = day.stop_ids.filtered(lambda s: s.origin == "template" and s.state == "todo")
                manual = day.stop_ids - from_template
                from_template.unlink()
                vals = {"user_id": route.user_id.id or day.user_id.id,
                        "vehicle_id": route.vehicle_id.id or day.vehicle_id.id,
                        "date_planned_start": local_to_utc(route._tz(), day.date, route.start_hour)}
                kept_templates = manual.mapped("template_stop_id")
                vals["stop_ids"] = [(0, 0, stop._day_stop_vals()) for stop in route.stop_ids
                                    if stop not in kept_templates]
                day.write(vals)
                # Hand-added stops go after the template's, in their own order.
                last = max(day.stop_ids.filtered("template_stop_id").mapped("sequence") or [0])
                for offset, stop in enumerate(manual.filtered(lambda s: s.origin != "template"), 1):
                    stop.sequence = last + offset

    def write(self, vals):
        result = super().write(vals)
        if self.env.context.get("bf_route_no_refresh"):
            return result
        if CALENDAR_FIELDS & set(vals):
            self._drop_obsolete_days()
            self._generate_days()
        if DAY_SOURCE_FIELDS & set(vals):
            self._refresh_upcoming_days()
        return result

    def action_generate_days(self):
        self._check_manager()
        created = self._generate_days()
        return self.env["bf.route.day"]._notify(
            _("%s route day(s) created.", len(created)) if created
            else _("Every day to come already exists."))

    def action_plan_day(self):
        """A day of an on-demand route (or an extra day of a weekly one)."""
        self.ensure_one()
        self._check_manager()
        return {
            "type": "ir.actions.act_window",
            "res_model": "bf.route.day",
            "view_mode": "form",
            "target": "current",
            "context": {"default_route_id": self.id, "bf_route_copy_stops": True},
        }

    def action_open_days(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id("bf_route.action_bf_route_day")
        action["domain"] = [("route_id", "=", self.id)]
        action["context"] = {"default_route_id": self.id}
        return action

    # ------------------------------------------------------------------
    # Routing engine
    # ------------------------------------------------------------------
    def _engine_points(self, stops):
        self.ensure_one()
        start = self.start_partner_id or self.company_id.partner_id
        end = self.end_partner_id or start
        return self.env["bf.route.engine"]._points(start, stops.mapped("partner_id"), end)

    def action_compute_distance(self):
        self._check_manager()
        for route in self:
            if not route.stop_ids:
                raise UserError(_("The route has no stop."))
            # ⚠️ sorted(): after a reorder, the cached One2many keeps the OLD order.
            stops = route.stop_ids.sorted("sequence")
            plan = self.env["bf.route.engine"]._route(route._engine_points(stops))
            route.with_context(bf_route_no_refresh=True).write({
                "distance_km": plan["distance"] / 1000.0,
                "duration_minutes": round(plan["duration"] / 60.0),
            })

    def action_optimize(self):
        """Reorder the template's stops with VROOM. Time windows are seconds of the day."""
        self._check_manager()
        for route in self:
            if len(route.stop_ids) < 2:
                raise UserError(_("There is nothing to reorder with fewer than two stops."))
            start = route.start_partner_id or route.company_id.partner_id
            end = route.end_partner_id or start
            order = self.env["bf.route.engine"]._optimize(
                start, end, route.stop_ids.sorted("sequence"),
                start_seconds=int(route.start_hour * 3600))
            for rank, stop in enumerate(order["ordered"], 1):
                stop.sequence = rank * 10
            # ⚠️ sudo: outside sudo, Odoo refuses a note whose author has no email address
            # (mail_thread._message_compute_author), and a route manager may have none.
            route.sudo().message_post(body=order["summary"])
        self._refresh_upcoming_days()
        self.action_compute_distance()

    # ------------------------------------------------------------------
    # Watcher
    # ------------------------------------------------------------------
    @api.model
    def _cron_watch(self):
        """Three jobs, each in its own savepoint: one that fails (a bad setting, an odd
        record) must not stop the others, least of all the erasing of old positions."""
        jobs = [
            ("days", lambda: self.sudo().search([("recurrence", "=", "weekly")])._generate_days()),
            ("alerts", lambda: self.env["bf.route.day"].sudo()._watch()),
            ("positions", lambda: self.env["bf.route.day.stop"].sudo()._purge_positions()),
        ]
        for name, job in jobs:
            try:
                with self.env.cr.savepoint():
                    job()
            except Exception:
                _logger.exception("bf_route: the watcher's %s job failed", name)
                self.env.invalidate_all()


class BfRouteStop(models.Model):
    _name = "bf.route.stop"
    _description = "Stop of a work route"
    _order = "route_id, sequence, id"

    route_id = fields.Many2one("bf.route", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="route_id.company_id", store=True)
    sequence = fields.Integer(default=10)
    partner_id = fields.Many2one("res.partner", string="Customer", required=True)
    address = fields.Char(related="partner_id.contact_address", string="Address")
    latitude = fields.Float(related="partner_id.partner_latitude", readonly=False)
    longitude = fields.Float(related="partner_id.partner_longitude", readonly=False)
    window_start = fields.Float(string="Not before", help="Local time. Empty = any time.")
    window_end = fields.Float(string="Not after", help="Local time. Empty = any time.")
    service_minutes = fields.Integer(string="Time on site (min)", default=10)
    instructions = fields.Text(string="Access instructions")

    _sql_constraints = [
        ("service_positive", "CHECK(service_minutes >= 0)", "The time on site cannot be negative."),
    ]

    @api.constrains("window_start", "window_end")
    def _check_window(self):
        for stop in self:
            if stop.window_start and stop.window_end and stop.window_end <= stop.window_start:
                raise ValidationError(_("The time window of %s ends before it starts.",
                                        stop.partner_id.display_name))

    def _compute_display_name(self):
        for stop in self:
            stop.display_name = "%s · %s" % (stop.route_id.name, stop.partner_id.display_name)

    def unlink(self):
        """A customer taken off the route leaves the planned days that still follow the route.
        A day the office edited keeps it, with a note and an activity: the office decides."""
        Stop = self.env["bf.route.day.stop"].sudo()
        copies = Stop.search([("template_stop_id", "in", self.ids), ("state", "=", "todo"),
                              ("day_id.state", "=", "planned")])
        auto = copies.filtered(lambda s: not s.day_id._office_prepared())
        for day in (copies - auto).day_id:
            names = ", ".join((copies - auto).filtered(lambda s: s.day_id == day).mapped(
                "partner_id.display_name"))
            day.message_post(body=_("Taken off the route: %s. Still on this day, edited by hand.", names))
            day._alert(lambda e: e._("Customer taken off the route: %s", day.name),
                       lambda e: e._("Still on this day, edited by hand: %s.", names))
        auto.with_context(bf_route_auto=True).unlink()
        return super().unlink()

    def _day_stop_vals(self):
        self.ensure_one()
        return {
            "origin": "template",
            "template_stop_id": self.id,
            "sequence": self.sequence,
            "partner_id": self.partner_id.id,
            "window_start": self.window_start,
            "window_end": self.window_end,
            "service_minutes": self.service_minutes,
            "instructions": self.instructions,
        }
