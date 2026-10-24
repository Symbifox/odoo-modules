from datetime import datetime, time, timedelta

import pytz

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools import format_date

KINDS = [
    ("class", "Class day"),
    ("pedagogical", "Pedagogical day"),
    ("break", "School break"),
    ("holiday", "Holiday (closed)"),
    ("closure", "Emergency closure"),
]
#: Child care opens by default on these days. On a school break the office may open it.
CHILDCARE_OPEN_KINDS = ("class", "pedagogical")
#: A change of these fields may stop what was booked on a day.
CHANGES = {"kind", "date", "school_id", "childcare_open"}
#: The schools of the suite are in Québec: the company's own time zone wins when it has one.
DEFAULT_TZ = "America/Toronto"


class School(models.Model):
    _inherit = "bf.school"

    # --- The school's clock ----------------------------------------------------------------
    # 🔴 "Today" and "now" are the school's, never the server's (UTC): a lateness test ran
    # between 8 p.m. and midnight once and read the next day. Every school module that needs
    # a date or a time of day asks here.

    def _calendar_tz(self):
        self.ensure_one()
        return pytz.timezone(self.sudo().company_id.partner_id.tz or DEFAULT_TZ)

    def _calendar_now(self):
        """Now, as an aware datetime in the school's time zone."""
        return datetime.now(pytz.utc).astimezone(self._calendar_tz())

    def _calendar_today(self):
        return self._calendar_now().date()

    def _calendar_local(self, value):
        """A stored (naive UTC) datetime, as an aware datetime in the school's time zone."""
        self.ensure_one()
        return pytz.utc.localize(value).astimezone(self._calendar_tz()) if value else False

    def _calendar_at(self, day, hour):
        """The naive UTC datetime of a time of day (hours as a float: 15.5 is 15:30) on a date,
        read on the school's clock."""
        self.ensure_one()
        local = datetime.combine(day, time()) + timedelta(minutes=round((hour or 0.0) * 60))
        return self._calendar_tz().localize(local).astimezone(pytz.utc).replace(tzinfo=None)

    # --- The calendar ----------------------------------------------------------------------

    def _calendar_day(self, day):
        """The day of this school's calendar on `day`, read in sudo: the calendar is not
        secret, and the family portal and the child care staff both ask it."""
        self.ensure_one()
        return self.env["bf.school.calendar.day"].sudo().search(
            [("school_id", "=", self.id), ("date", "=", day)], limit=1)

    def _calendar_childcare_open(self, day):
        """🔴 A date the calendar does not hold is closed: child care opens only on a day the
        school has set."""
        return bool(self._calendar_day(day).childcare_open)

    def _calendar_is_class_day(self, day):
        return self._calendar_day(day).kind == "class"


class SchoolYear(models.Model):
    _inherit = "bf.school.year"

    # Staff only: the child care roles read the years without being staff.
    calendar_day_ids = fields.One2many("bf.school.calendar.day", "year_id", "Calendar",
                                       groups="bf_school_core.group_school_user")
    calendar_day_count = fields.Integer("Days in the calendar", compute="_compute_calendar_day_count",
                                        groups="bf_school_core.group_school_user")

    def _compute_calendar_day_count(self):
        counts = dict(self.env["bf.school.calendar.day"]._read_group(
            [("year_id", "in", self.ids)], ["year_id"], ["__count"]))
        for year in self:
            year.calendar_day_count = counts.get(year, 0)

    def write(self, vals):
        schools = self.school_id
        result = super().write(vals)
        if {"date_start", "date_end", "school_id"} & set(vals):
            # 🔴 A shortened year left days outside it still "open": their year is computed again
            # and Odoo runs _check_year on that recompute: a day no year covers is refused.
            Day = self.env["bf.school.calendar.day"].sudo()
            days = Day.search([("school_id", "in", (schools | self.school_id).ids)])
            self.env.add_to_compute(Day._fields["year_id"], days)
            days.flush_recordset(["year_id"])
        return result

    def action_school_generate_calendar(self):
        """One class day for each weekday of the year that the calendar does not hold yet.

        The days already set (a pedagogical day, a holiday) are never touched: the button may
        be pressed again after a change of the year's dates.
        """
        Day = self.env["bf.school.calendar.day"]
        created = 0
        for year in self:
            year.check_access("write")
            Day.check_access("create")
            have = set(Day.sudo().search([
                ("school_id", "=", year.school_id.id),
                ("date", ">=", year.date_start), ("date", "<=", year.date_end)]).mapped("date"))
            vals, day = [], year.date_start
            while day <= year.date_end:
                if day.weekday() < 5 and day not in have:
                    vals.append({"school_id": year.school_id.id, "date": day, "kind": "class"})
                day += timedelta(days=1)
            created += len(Day.sudo().create(vals))
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("School calendar"),
                "message": _("%s class day(s) added. Mark the pedagogical days, breaks and holidays "
                             "in the calendar.", created),
                "type": "success",
                "sticky": False,
            },
        }

    def action_school_view_calendar(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id("bf_school_calendar.bf_school_calendar_day_action")
        action["domain"] = [("year_id", "=", self.id)]
        action["context"] = {"default_school_id": self.school_id.id}
        return action


class SchoolCalendarDay(models.Model):
    """One date of a school's calendar and what kind of day it is."""

    _name = "bf.school.calendar.day"
    _description = "Day of the school calendar"
    _order = "date, school_id"

    school_id = fields.Many2one("bf.school", "School", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="school_id.company_id", store=True, index=True)
    year_id = fields.Many2one(
        "bf.school.year", "School year", compute="_compute_year_id", store=True, index=True,
        ondelete="cascade", help="The school year of the school that covers this date.")
    date = fields.Date(required=True, index=True)
    weekday = fields.Char("Day of the week", compute="_compute_weekday")
    kind = fields.Selection(KINDS, "Kind of day", required=True, default="class")
    childcare_open = fields.Boolean(
        "Child care open", compute="_compute_childcare_open", store=True, readonly=False,
        help="Yes by default on class and pedagogical days. The office may open child care "
             "on a school break, or close it on any day.")
    early_dismissal_time = fields.Float(
        "Early dismissal", help="The time classes end that day when it is earlier than usual. "
                                "Empty (00:00) when classes end at the usual time.")
    note = fields.Char()

    _sql_constraints = [
        ("school_date_unique", "UNIQUE(school_id, date)", "A date appears once in a school's calendar."),
    ]

    @api.depends("school_id", "date")
    def _compute_year_id(self):
        Year = self.env["bf.school.year"].sudo()
        for day in self:
            year = Year.browse()
            if day.school_id and day.date:
                year = Year.search([("school_id", "=", day.school_id.id),
                                    ("date_start", "<=", day.date), ("date_end", ">=", day.date)], limit=1)
            day.year_id = year

    @api.depends("date")
    @api.depends_context("lang")
    def _compute_weekday(self):
        for day in self:
            weekday = format_date(self.env, day.date, date_format="EEEE") if day.date else ""
            day.weekday = weekday[:1].upper() + weekday[1:]

    @api.depends("kind")
    def _compute_childcare_open(self):
        for day in self:
            day.childcare_open = day.kind in CHILDCARE_OPEN_KINDS

    @api.depends("date", "kind")
    @api.depends_context("lang")
    def _compute_display_name(self):
        kinds = dict(self._fields["kind"]._description_selection(self.env))
        for day in self:
            day.display_name = " · ".join(filter(None, [
                format_date(self.env, day.date) if day.date else "", kinds.get(day.kind)]))

    @api.constrains("school_id", "date", "year_id")
    def _check_year(self):
        for day in self:
            if not day.year_id:
                raise ValidationError(_("No school year of %(school)s covers %(date)s: create the year first.",
                                        school=day.school_id.name, date=format_date(self.env, day.date)))

    @api.constrains("early_dismissal_time")
    def _check_early_dismissal_time(self):
        for day in self:
            if not 0 <= day.early_dismissal_time < 24:
                raise ValidationError(_("An early dismissal is a time of day, between 00:00 and 23:59."))

    def write(self, vals):
        was_open = self.filtered("childcare_open")
        before = [(day.school_id, day.date) for day in self] if CHANGES & set(vals) else []
        result = super().write(vals)
        closed = was_open.filtered(lambda d: not d.childcare_open)
        if closed:
            closed._school_childcare_closed()
        if before:
            self._school_calendar_changed(before)
        return result

    def unlink(self):
        # A date the calendar no longer holds is closed (see bf.school._calendar_childcare_open).
        opened = self.filtered("childcare_open")
        if opened:
            opened._school_childcare_closed()
        return super().unlink()

    def _school_calendar_changed(self, before):
        """These days changed kind, date or school; `before` holds their (school, date) before the
        change. Nothing to do here: the modules that book on the calendar check their bookings."""
        return True

    def _school_childcare_closed(self):
        """Child care no longer opens on these days (storm, closure, a class day turned into a
        holiday). Nothing to do here: the modules that book child care close what was booked."""
        return True
