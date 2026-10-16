from datetime import datetime, time, timedelta

import pytz

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError


def float_to_time(value):
    """Hours as a float (8.5 is 8:30) to a time of day; whole minutes, so 8.999 is 9:00 and not 8:60."""
    minutes = int(round(value * 60))
    return time(minutes // 60 % 24, minutes % 60)


class SchoolRoom(models.Model):
    """A room of the school: a class is held in one at a time."""

    _name = "bf.school.room"
    _description = "School room"
    _order = "school_id, name"

    name = fields.Char(required=True)
    code = fields.Char()
    school_id = fields.Many2one("bf.school", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="school_id.company_id", store=True)
    capacity = fields.Integer()
    active = fields.Boolean(default=True)

    _sql_constraints = [("name_school_unique", "UNIQUE(school_id, name)", "A room name is used once per school.")]


class SchoolTimetable(models.Model):
    """The timetable of a school year: periods, a cycle of days, and what is taught when.

    Québec schools often run a cycle of 9 or 10 school days rather than a week, so the timetable
    says "day 3, period 2". The calendar says which date is which day of the cycle, and skips the
    holidays and pedagogical days without losing its place.
    """

    _name = "bf.school.timetable"
    _description = "Timetable"
    _inherit = ["mail.thread"]
    _order = "year_id desc, id desc"

    name = fields.Char(required=True, tracking=True, default=lambda s: _("Timetable"))
    school_id = fields.Many2one("bf.school", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="school_id.company_id", store=True)
    year_id = fields.Many2one("bf.school.year", "School year", required=True, ondelete="restrict",
                              domain="[('school_id', '=', school_id)]")
    cycle_days = fields.Integer("Days in the cycle", default=9, required=True,
                                help="A cycle of 9 or 10 school days, or 5 for a plain week.")
    first_cycle_day = fields.Integer("Day of the cycle on the first day", default=1)
    date_start = fields.Date("Starts", required=True)
    date_end = fields.Date("Ends", required=True)
    state = fields.Selection([("draft", "Draft"), ("published", "Published")], default="draft", required=True,
                             tracking=True,
                             help="Families see a published timetable only.")
    period_ids = fields.One2many("bf.school.period", "timetable_id", "Periods")
    day_ids = fields.One2many("bf.school.timetable.day", "timetable_id", "Calendar")
    slot_ids = fields.One2many("bf.school.timetable.slot", "timetable_id", "Classes")
    slot_count = fields.Integer(compute="_compute_slot_count")

    _sql_constraints = [
        ("cycle_range", "CHECK(cycle_days BETWEEN 1 AND 20)", "A cycle has between 1 and 20 days."),
    ]

    @api.depends("slot_ids")
    def _compute_slot_count(self):
        for timetable in self:
            timetable.slot_count = len(timetable.slot_ids)

    @api.onchange("year_id")
    def _onchange_year_id(self):
        if self.year_id:
            self.date_start, self.date_end = self.year_id.date_start, self.year_id.date_end
            self.school_id = self.year_id.school_id

    @api.constrains("date_start", "date_end", "first_cycle_day", "cycle_days")
    def _check_dates(self):
        for timetable in self:
            if timetable.date_end < timetable.date_start:
                raise ValidationError(_("A timetable ends after it starts."))
            if not 1 <= timetable.first_cycle_day <= timetable.cycle_days:
                raise ValidationError(_("The day of the cycle on the first day is between 1 and %s.", timetable.cycle_days))

    @api.constrains("cycle_days")
    def _check_slots_in_cycle(self):
        for timetable in self:
            beyond = timetable.slot_ids.filtered(lambda s: s.day_no > timetable.cycle_days)
            if beyond:
                raise ValidationError(_(
                    "%(slot)s is held on a day beyond the cycle of %(days)s days: move it first.",
                    slot=beyond[0].display_name, days=timetable.cycle_days))

    def write(self, vals):
        result = super().write(vals)
        if {"first_cycle_day", "cycle_days"} & set(vals):
            # The days already have their numbers: a new start or a new length renumbers them all.
            for timetable in self:
                timetable._renumber()
        return result

    @api.constrains("school_id", "year_id")
    def _check_year(self):
        for timetable in self:
            if timetable.year_id.school_id != timetable.school_id:
                raise ValidationError(_("The school year belongs to another school."))

    # --- The calendar ---------------------------------------------------------------------------

    def action_generate_days(self):
        """One class day for each weekday between the dates that has no line yet, then number the cycle."""
        for timetable in self:
            timetable.check_access("write")
            have = set(timetable.day_ids.mapped("date"))
            day, vals = timetable.date_start, []
            while day <= timetable.date_end:
                if day.weekday() < 5 and day not in have:
                    vals.append({"timetable_id": timetable.id, "date": day, "kind": "class"})
                day += timedelta(days=1)
            # The calendar is made in one go, then numbered once: the days' numbers are the system's.
            self.env["bf.school.timetable.day"].sudo().with_context(skip_renumber=True).create(vals)
            timetable._renumber()
        return True

    def _renumber(self):
        """Give the class days their day of the cycle, in date order; the others have none."""
        self.ensure_one()
        number = self.first_cycle_day - 1
        for day in self.day_ids.sorted("date"):
            if day.kind in ("class", "exam"):
                number += 1
                cycle_day = (number - 1) % self.cycle_days + 1
            else:
                cycle_day = 0
            if day.cycle_day != cycle_day:
                day.sudo().with_context(skip_renumber=True).write({"cycle_day": cycle_day})

    # --- Reading it -------------------------------------------------------------------------------

    def _tz(self):
        self.ensure_one()
        return pytz.timezone(self.company_id.partner_id.tz or "America/Toronto")

    def _occurrences(self, date_from, date_to, slots):
        """What happens between two dates for these slots: dicts with local naive start and end."""
        self.ensure_one()
        by_day = {}
        for slot in slots:
            by_day.setdefault(slot.day_no, []).append(slot)
        out = []
        for day in self.day_ids.filtered(lambda d: d.cycle_day and date_from <= d.date <= date_to).sorted("date"):
            for slot in sorted(by_day.get(day.cycle_day, []), key=lambda s: s.period_id.start_time):
                period = slot.period_id
                out.append({
                    "date": day.date, "slot": slot, "course": slot.course_id, "room": slot.room_id,
                    "period": period, "cycle_day": day.cycle_day,
                    "start": datetime.combine(day.date, float_to_time(period.start_time)),
                    "end": datetime.combine(day.date, float_to_time(period.end_time))})
        return out

    @api.model
    def _published_by_date(self, school, date_from, date_to):
        """[(timetable, {dates})]: the published timetable in force on each date of the window (the latest made
        when two cover the same date). A date no timetable covers is in none, and a draft is never in force."""
        candidates = self.search([
            ("school_id", "=", school.id), ("state", "=", "published"),
            ("date_start", "<=", date_to), ("date_end", ">=", date_from)], order="id desc")
        by_timetable = {}
        day = date_from
        while day <= date_to:
            timetable = next((t for t in candidates if t.date_start <= day <= t.date_end), None)
            if timetable:
                by_timetable.setdefault(timetable, set()).add(day)
            day += timedelta(days=1)
        return list(by_timetable.items())

    @api.model
    def _timetable_to_print(self, group):
        """The timetable a group's printed copy shows: the published one in force today, else the next one to start,
        else the last one that was in force. Never a draft: a printed copy goes to the families."""
        today = fields.Date.context_today(self)
        candidates = self.search([("school_id", "=", group.school_id.id), ("year_id", "=", group.year_id.id),
                                  ("state", "=", "published")], order="id desc")
        for timetable in candidates:
            if timetable.date_start <= today <= timetable.date_end:
                return timetable
        upcoming = candidates.filtered(lambda t: t.date_start > today).sorted("date_start")
        if upcoming:
            return upcoming[0]
        return candidates.sorted("date_end", reverse=True)[:1]

    def action_publish(self):
        for timetable in self:
            if not timetable.slot_ids:
                raise ValidationError(_("There is no class in this timetable yet."))
            if not timetable.day_ids:
                raise ValidationError(_("Generate the calendar before publishing: families see dates."))
        self.write({"state": "published"})
        return True

    def action_open_import(self):
        self.ensure_one()
        return {"type": "ir.actions.act_window", "name": _("Import classes from a file"),
                "res_model": "bf.school.timetable.import", "view_mode": "form", "target": "new",
                "context": {"default_timetable_id": self.id}}

    def action_draft(self):
        self.write({"state": "draft"})
        return True


class SchoolPeriod(models.Model):
    """A period of the day: from a time to a time."""

    _name = "bf.school.period"
    _description = "Period of the day"
    _order = "timetable_id, start_time, id"

    timetable_id = fields.Many2one("bf.school.timetable", required=True, ondelete="cascade", index=True)
    name = fields.Char(required=True, help="For example Period 1, or Lunch.")
    start_time = fields.Float(required=True, help="Time of day.")
    end_time = fields.Float(required=True)
    is_break = fields.Boolean("Break", help="A break holds no class: it only shows on the timetable.")
    start_label = fields.Char("Start time (text)", compute="_compute_labels")
    end_label = fields.Char("End time (text)", compute="_compute_labels")

    @api.depends("start_time", "end_time")
    def _compute_labels(self):
        for period in self:
            period.start_label = float_to_time(period.start_time).strftime("%H:%M")
            period.end_label = float_to_time(period.end_time).strftime("%H:%M")

    @api.constrains("start_time", "end_time")
    def _check_times(self):
        for period in self:
            start, end = round(period.start_time * 60), round(period.end_time * 60)
            if not (0 <= start < end < 24 * 60):
                raise ValidationError(_("A period ends after it starts, on the same day."))

    @api.constrains("start_time", "end_time", "timetable_id")
    def _check_no_overlap(self):
        """Two periods of a day do not overlap: a class would be in both at once."""
        for period in self:
            start, end = round(period.start_time * 60), round(period.end_time * 60)
            for other in period.timetable_id.period_ids - period:
                o_start, o_end = round(other.start_time * 60), round(other.end_time * 60)
                if start < o_end and o_start < end:
                    raise ValidationError(_(
                        "%(period)s overlaps %(other)s: two periods of a day do not share a minute.",
                        period=period.name, other=other.name))


class SchoolTimetableDay(models.Model):
    """A date of the calendar and what kind of day it is."""

    _name = "bf.school.timetable.day"
    _description = "Day of the timetable calendar"
    _order = "timetable_id, date"

    timetable_id = fields.Many2one("bf.school.timetable", required=True, ondelete="cascade", index=True)
    date = fields.Date(required=True, index=True)
    kind = fields.Selection(
        [("class", "Class day"), ("exam", "Exam day"), ("pedagogical", "Pedagogical day"),
         ("holiday", "Holiday or break")], required=True, default="class",
        help="Pedagogical days and holidays hold no class and do not use a day of the cycle.")
    cycle_day = fields.Integer("Day of the cycle", readonly=True, help="0 when there is no class.")
    note = fields.Char()

    _sql_constraints = [("timetable_date_unique", "UNIQUE(timetable_id, date)", "A date appears once in a calendar.")]

    def _renumbered_by_the_system(self):
        """The calendar's own batch operations say so in the context; a client's context is not believed."""
        return self.env.su and bool(self.env.context.get("skip_renumber"))

    @api.model_create_multi
    def create(self, vals_list):
        # 🔴 `readonly` is no guard against RPC: the day of the cycle is computed, never typed.
        if not self.env.su and any("cycle_day" in vals for vals in vals_list):
            raise AccessError(_("The day of the cycle follows from the calendar: it is not entered."))
        days = super().create(vals_list)
        if not self._renumbered_by_the_system():
            days.timetable_id._renumber()
        return days

    def write(self, vals):
        if "cycle_day" in vals and not self.env.su:
            raise AccessError(_("The day of the cycle follows from the calendar: it is not entered."))
        timetables = self.timetable_id
        result = super().write(vals)
        if {"kind", "date", "timetable_id"} & set(vals) and not self._renumbered_by_the_system():
            (timetables | self.timetable_id)._renumber()
        return result

    def unlink(self):
        timetables = self.timetable_id
        result = super().unlink()
        timetables._renumber()
        return result


class SchoolTimetableSlot(models.Model):
    """A course held on a day of the cycle in a period, in a room."""

    _name = "bf.school.timetable.slot"
    _description = "Class in the timetable"
    _order = "timetable_id, day_no, period_id, id"

    timetable_id = fields.Many2one("bf.school.timetable", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="timetable_id.company_id", store=True)
    day_no = fields.Integer("Day of the cycle", required=True)
    period_id = fields.Many2one("bf.school.period", required=True, ondelete="restrict",
                                domain="[('timetable_id', '=', timetable_id), ('is_break', '=', False)]")
    course_id = fields.Many2one("bf.school.course", required=True, ondelete="cascade", index=True)
    group_id = fields.Many2one(related="course_id.group_id", store=True, index=True)
    teacher_ids = fields.Many2many(related="course_id.teacher_ids")
    room_id = fields.Many2one("bf.school.room", ondelete="set null", domain="[('school_id', '=', school_id)]")
    school_id = fields.Many2one(related="timetable_id.school_id", store=True)
    allow_overlap = fields.Boolean(
        "Shares the group", help="The group splits for this class (options, languages): another class of the "
                                 "group may be held at the same time.")

    _sql_constraints = [
        ("slot_unique", "UNIQUE(timetable_id, day_no, period_id, course_id)", "This class is already in that period."),
    ]

    @api.depends_context("lang")
    @api.depends("day_no", "period_id.name", "course_id.display_name")
    def _compute_display_name(self):
        for slot in self:
            slot.display_name = _("Day %(day)s, %(period)s: %(course)s", day=slot.day_no,
                                  period=slot.period_id.name or "", course=slot.course_id.display_name or "")

    @api.constrains("day_no", "timetable_id")
    def _check_day(self):
        for slot in self:
            if not 1 <= slot.day_no <= slot.timetable_id.cycle_days:
                raise ValidationError(_("The day of the cycle is between 1 and %s.", slot.timetable_id.cycle_days))

    @api.constrains("period_id", "course_id", "room_id", "timetable_id")
    def _check_links(self):
        for slot in self:
            if slot.period_id.timetable_id != slot.timetable_id:
                raise ValidationError(_("The period belongs to another timetable."))
            if slot.period_id.is_break:
                raise ValidationError(_("A break holds no class."))
            if slot.course_id.year_id != slot.timetable_id.year_id:
                raise ValidationError(_("%s is not a course of this school year.", slot.course_id.display_name))
            if slot.room_id and slot.room_id.school_id != slot.timetable_id.school_id:
                raise ValidationError(_("The room belongs to another school."))

    @api.constrains("day_no", "period_id", "course_id", "room_id", "allow_overlap")
    def _check_conflicts(self):
        """Nobody, no room and no group is in two places at once."""
        for slot in self:
            others = self.search([
                ("timetable_id", "=", slot.timetable_id.id), ("day_no", "=", slot.day_no),
                ("period_id", "=", slot.period_id.id), ("id", "!=", slot.id)])
            teachers = slot.course_id.teacher_ids
            for other in others:
                shared = teachers & other.course_id.teacher_ids
                if shared:
                    raise ValidationError(_(
                        "%(teacher)s is already teaching %(course)s on day %(day)s, %(period)s.",
                        teacher=shared[0].name, course=other.course_id.display_name, day=slot.day_no,
                        period=slot.period_id.name))
                if slot.room_id and slot.room_id == other.room_id:
                    raise ValidationError(_(
                        "%(room)s is already used by %(course)s on day %(day)s, %(period)s.",
                        room=slot.room_id.name, course=other.course_id.display_name, day=slot.day_no,
                        period=slot.period_id.name))
                if slot.group_id == other.group_id and not (slot.allow_overlap and other.allow_overlap):
                    raise ValidationError(_(
                        "The group %(group)s already has %(course)s on day %(day)s, %(period)s. Tick \"Shares the "
                        "group\" on both when the group splits.", group=slot.group_id.name,
                        course=other.course_id.display_name, day=slot.day_no, period=slot.period_id.name))


class SchoolCourse(models.Model):
    _inherit = "bf.school.course"

    def write(self, vals):
        result = super().write(vals)
        if {"teacher_ids", "group_id"} & set(vals):
            # A teacher added to a course can be in two places at once: the timetable says so now, not at the next
            # time someone touches a class.
            self.env["bf.school.timetable.slot"].sudo().search([("course_id", "in", self.ids)])._check_conflicts()
        return result
