from collections import defaultdict

from odoo import _, api, fields, models
from odoo.exceptions import UserError

#: The kind of a timetable day for each kind of day of the school calendar.
CALENDAR_TO_TIMETABLE = {
    "class": "class",
    "pedagogical": "pedagogical",
    "break": "holiday",
    "holiday": "holiday",
    "closure": "holiday",
}
#: The other way, for a school whose days were kept in a timetable before (an exam day is a class day).
TIMETABLE_TO_CALENDAR = {"class": "class", "exam": "class", "pedagogical": "pedagogical", "holiday": "holiday"}
#: The only change made to a day in a timetable that follows the calendar.
TEACHING_KINDS = ("class", "exam")
#: A change of these fields of a timetable reads the calendar again.
RANGE_FIELDS = {"school_id", "year_id", "date_start", "date_end"}


#: Set while the calendar takes the days of a timetable: the timetables are read once after. 🔴 Its value is
#: an object no request can send (a caller's context would otherwise reach the sudo of the calendar's own button).
SEEDING = "school_calendar_seeding"
SEEDING_TOKEN = object()


def _seed_school_year(env, school, year):
    """The calendar of (school, year) takes the days its timetables hold (a published timetable read
    first): a date it does not hold yet comes in, and a class day the calendar just made takes the
    timetable's kind (a pedagogical day, a holiday). A date of another school year is left out."""
    Day = env["bf.school.calendar.day"].sudo().with_context(**{SEEDING: SEEDING_TOKEN})
    wanted = {}
    timetables = env["bf.school.timetable"].sudo().search([("school_id", "=", school.id), ("year_id", "=", year.id)])
    for timetable in timetables.sorted(lambda t: (t.state != "published", t.id)):
        for day in timetable.day_ids:
            if year.date_start <= day.date <= year.date_end:
                wanted.setdefault(day.date, TIMETABLE_TO_CALENDAR.get(day.kind, "holiday"))
    if not wanted:
        return
    have = {day.date: day for day in Day.search([("school_id", "=", school.id), ("date", "in", list(wanted))])}
    for date, kind in wanted.items():
        day = have.get(date)
        if day and day.year_id == year and day.kind == "class" and kind != "class":
            day.write({"kind": kind})
    Day.create([{"school_id": school.id, "date": date, "kind": kind}
                for date, kind in sorted(wanted.items()) if date not in have])


def _seed_calendar_from_timetables(env):
    """At install, a school that kept its days in a timetable keeps them: the school calendar takes
    them, for each school year it holds no day of yet. Then every timetable reads the calendar."""
    kept = {(day.school_id.id, day.year_id.id) for day in env["bf.school.calendar.day"].sudo().search([])}
    timetables = env["bf.school.timetable"].sudo().search([])
    for school, year in {(t.school_id, t.year_id) for t in timetables}:
        if (school.id, year.id) not in kept:
            _seed_school_year(env, school, year)
    timetables._calendar_sync()


class SchoolTimetable(models.Model):
    _inherit = "bf.school.timetable"

    calendar_kept = fields.Boolean(
        "Follows the school calendar", compute="_compute_calendar_kept",
        help="The school keeps its calendar for this year: the days of the timetable follow it.")

    def _compute_calendar_kept(self):
        for timetable in self:
            timetable.calendar_kept = timetable._calendar_kept()

    def _calendar_kept(self):
        """Does the school keep its calendar for the year of this timetable?"""
        self.ensure_one()
        timetable = self.sudo()
        return bool(timetable.school_id and timetable.year_id and self.env["bf.school.calendar.day"].sudo().search_count(
            [("school_id", "=", timetable.school_id.id), ("year_id", "=", timetable.year_id.id)], limit=1))

    def _calendar_sync(self):
        """The days of these timetables become those of the school calendar between their dates. An
        exam day stays one while the calendar holds a class day on that date."""
        Calendar = self.env["bf.school.calendar.day"].sudo()
        for timetable in self.sudo():
            if not timetable._calendar_kept():
                continue
            calendar = {day.date: CALENDAR_TO_TIMETABLE.get(day.kind, "holiday") for day in Calendar.search([
                ("school_id", "=", timetable.school_id.id),
                ("date", ">=", timetable.date_start), ("date", "<=", timetable.date_end)])}
            days = timetable.day_ids.with_context(skip_renumber=True)
            stale = days.filtered(lambda d: d.date not in calendar)
            if stale:  # the timetable's unlink numbers its days again: never on an empty set
                stale.unlink()
            have = {day.date: day for day in days.exists()}
            for date, kind in calendar.items():
                day = have.get(date)
                if day and day.kind != kind and not (kind == "class" and day.kind == "exam"):
                    day.write({"kind": kind})
            days.create([{"timetable_id": timetable.id, "date": date, "kind": kind}
                         for date, kind in sorted(calendar.items()) if date not in have])
            timetable._renumber()

    @api.model_create_multi
    def create(self, vals_list):
        timetables = super().create(vals_list)
        timetables._calendar_sync()
        return timetables

    def write(self, vals):
        result = super().write(vals)
        if RANGE_FIELDS & set(vals):
            self._calendar_sync()
        return result

    def action_generate_days(self):
        """A school that keeps its calendar gives the timetable its days; otherwise one class day for
        each weekday, as before."""
        kept = self.filtered(lambda t: t._calendar_kept())
        for timetable in kept:
            timetable.check_access("write")
        kept._calendar_sync()
        others = self - kept
        return super(SchoolTimetable, others).action_generate_days() if others else True


class SchoolTimetableDay(models.Model):
    _inherit = "bf.school.timetable.day"

    def _calendar_refuse(self):
        raise UserError(_("The days of this timetable follow the school calendar: change the day there. Here, "
                          "a class day only becomes an exam day, and back."))

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            timetables = self.env["bf.school.timetable"].browse(
                {vals.get("timetable_id") for vals in vals_list if vals.get("timetable_id")})
            if any(timetable._calendar_kept() for timetable in timetables):
                self._calendar_refuse()
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su:
            kept = self.filtered(lambda d: d.timetable_id._calendar_kept())
            if kept and (set(vals) - {"kind", "note"} or "kind" in vals and (
                    vals["kind"] not in TEACHING_KINDS or any(day.kind not in TEACHING_KINDS for day in kept))):
                self._calendar_refuse()
        return super().write(vals)

    def unlink(self):
        if not self.env.su and any(day.timetable_id._calendar_kept() for day in self):
            self._calendar_refuse()
        return super().unlink()


class SchoolCalendarDay(models.Model):
    _inherit = "bf.school.calendar.day"

    def _school_seeding(self):
        return self.env.su and self.env.context.get(SEEDING) is SEEDING_TOKEN

    @api.model_create_multi
    def create(self, vals_list):
        schools = {vals["school_id"] for vals in vals_list if vals.get("school_id")}
        kept = {(school.id, year.id) for school, year in self.sudo()._read_group(
            [("school_id", "in", list(schools))], ["school_id", "year_id"])} if schools else set()
        days = super().create(vals_list)
        if self._school_seeding():
            return days
        # 🔴 The first days of a year in the calendar take what its timetables already hold: generated as
        # class days, they would turn every pedagogical day and holiday of a published timetable into class.
        for school, year in {(day.school_id, day.year_id) for day in days}:
            if year and (school.id, year.id) not in kept:
                _seed_school_year(self.env, school, year)
                self.env["bf.school.timetable"].sudo().search(
                    [("school_id", "=", school.id), ("year_id", "=", year.id)])._calendar_sync()
        self._timetable_sync([(day.school_id, day.date) for day in days])
        return days

    def _school_calendar_changed(self, before):
        result = super()._school_calendar_changed(before)
        if not self._school_seeding():
            self._timetable_sync(list(before) + [(day.school_id, day.date) for day in self])
        return result

    def unlink(self):
        changed = [(day.school_id, day.date) for day in self]
        result = super().unlink()
        self._timetable_sync(changed)
        return result

    @api.model
    def _timetable_sync(self, changed):
        """The timetables of these schools that cover these dates read the calendar again."""
        dates = defaultdict(list)
        for school, date in changed:
            if school and date:
                dates[school.id].append(date)
        Timetable = self.env["bf.school.timetable"].sudo()
        timetables = Timetable.browse()
        for school_id, values in dates.items():
            timetables |= Timetable.search([("school_id", "=", school_id),
                                            ("date_start", "<=", max(values)), ("date_end", ">=", min(values))])
        timetables._calendar_sync()
