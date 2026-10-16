import base64
import csv
import io

from odoo import _, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

#: A timetable of a big school is a few thousand lines: more is not a timetable.
MAX_BYTES = 5 * 1024 * 1024
MAX_ROWS = 20000

#: The columns the file may have, by the names a timetable program's export usually gives them.
COLUMNS = {
    "group": ("group", "groupe", "classe", "class"),
    "subject": ("subject", "matiere", "matière", "course", "cours"),
    "day": ("day", "jour", "cycle day", "jour du cycle"),
    "period": ("period", "periode", "période", "slot", "plage"),
    "room": ("room", "salle", "local"),
}


class _Refused(Exception):
    """Inside a savepoint: a replacement that cannot be complete is undone whole."""


class TimetableImport(models.TransientModel):
    """Load classes into a timetable from a CSV file, as a timetable program (EDT, for one) can export.

    The columns are read by name (group, subject, day of the cycle, period, room), so a spreadsheet
    exported from any program works once its headings say that. The format EDT itself writes was not
    available to check: this reads a plain CSV and reports every line it could not place.
    """

    _name = "bf.school.timetable.import"
    _description = "Import a timetable from a CSV file"

    timetable_id = fields.Many2one("bf.school.timetable", required=True)
    file = fields.Binary(required=True)
    file_name = fields.Char()
    replace = fields.Boolean("Replace the classes of the groups in the file", default=False)
    create_rooms = fields.Boolean("Create the rooms that do not exist", default=True)
    log = fields.Text(readonly=True)

    def _rows(self):
        raw = base64.b64decode(self.file)
        if len(raw) > MAX_BYTES:
            raise UserError(_("The file is too big for a timetable: %s MB at most.", MAX_BYTES // (1024 * 1024)))
        for encoding in ("utf-8-sig", "latin-1"):
            try:
                text = raw.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        sample = text[:2048]
        delimiter = ";" if sample.count(";") > sample.count(",") else ","
        reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
        names = {(name or "").strip().lower(): name for name in (reader.fieldnames or [])}
        mapping = {}
        for key, aliases in COLUMNS.items():
            found = next((names[a] for a in aliases if a in names), None)
            if found:
                mapping[key] = found
        missing = [key for key in ("group", "subject", "day", "period") if key not in mapping]
        if missing:
            labels = {"group": _("group"), "subject": _("subject"), "day": _("day of the cycle"), "period": _("period")}
            raise UserError(_("The file has no column for: %s.", ", ".join(labels[key] for key in missing)))
        rows = [{key: (row.get(column) or "").strip() for key, column in mapping.items()} for row in reader]
        if len(rows) > MAX_ROWS:
            raise UserError(_("The file has more than %s lines.", MAX_ROWS))
        return rows

    def action_import(self):
        self.ensure_one()
        timetable = self.timetable_id
        Course, Slot = self.env["bf.school.course"], self.env["bf.school.timetable.slot"]
        periods = {}
        for period in timetable.period_ids:
            periods[period.name.strip().lower()] = period
        for index, period in enumerate(timetable.period_ids.filtered(lambda p: not p.is_break).sorted("start_time"), 1):
            periods.setdefault(str(index), period)
        rows, done, notes = self._rows(), 0, []
        groups = set()
        parsed = []
        courses = {}
        for number, row in enumerate(rows, start=2):
            key = (row["group"], row["subject"])
            if key not in courses:  # a timetable repeats the same course on many days: one search for each
                courses[key] = Course.search([
                    ("year_id", "=", timetable.year_id.id), ("group_id.name", "=", row["group"]),
                    "|", ("subject_id.code", "=", row["subject"]), ("subject_id.name", "=", row["subject"])], limit=1)
            course = courses[key]
            period = periods.get(row["period"].lower())
            try:
                day = int(row["day"])
            except ValueError:
                day = 0
            if not course:
                notes.append(_("Line %(n)s: no course %(subject)s for the group %(group)s.", n=number,
                               subject=row["subject"], group=row["group"]))
            elif not period:
                notes.append(_("Line %(n)s: no period %(period)s.", n=number, period=row["period"]))
            elif not 1 <= day <= timetable.cycle_days:
                notes.append(_("Line %(n)s: the day %(day)s is not in the cycle.", n=number, day=row["day"]))
            else:
                parsed.append((number, course, day, period, row.get("room")))
                groups.add(course.group_id.id)
        if self.replace and notes:
            # Replacing is all or nothing: a line that cannot be placed would leave its group without the class it had.
            self.log = _("Nothing was replaced: fix the lines below, then load the file again.") + "\n" + "\n".join(notes)
            return self._reopen()
        Room = self.env["bf.school.room"]
        # By name, whatever the case, and without the wildcards of a search: "A_101" is not "AB101".
        rooms = {r.name.strip().lower(): r for r in Room.with_context(active_test=False).search(
            [("school_id", "=", timetable.school_id.id)])}
        try:
            with self.env.cr.savepoint():
                if self.replace and groups:
                    timetable.slot_ids.filtered(lambda s: s.group_id.id in groups).unlink()
                # A class already in the timetable (the same file loaded twice) is said, not left to the database.
                have = {(s.day_no, s.period_id.id, s.course_id.id) for s in timetable.slot_ids}
                for number, course, day, period, room_name in parsed:
                    if (day, period.id, course.id) in have:
                        notes.append(_("Line %(n)s: this class is already in the timetable.", n=number))
                        continue
                    have.add((day, period.id, course.id))
                    room = rooms.get(room_name.strip().lower()) if room_name else Room
                    if room and not room.active:
                        notes.append(_("Line %(n)s: the room %(room)s is archived.", n=number, room=room.name))
                        continue
                    if room_name and not room and self.create_rooms:
                        room = rooms[room_name.strip().lower()] = Room.create(
                            {"school_id": timetable.school_id.id, "name": room_name})
                    room = room or Room
                    try:
                        with self.env.cr.savepoint():
                            Slot.create({"timetable_id": timetable.id, "day_no": day, "period_id": period.id,
                                         "course_id": course.id, "room_id": room.id or False})
                        done += 1
                    except (ValidationError, UserError, AccessError) as exc:
                        notes.append(_("Line %(n)s: %(why)s", n=number, why=str(exc.args[0]) if exc.args else str(exc)))
                if self.replace and notes:
                    raise _Refused()
        except _Refused:
            self.log = _("Nothing was replaced: fix the lines below, then load the file again.") + "\n" + "\n".join(notes)
            return self._reopen()
        self.log = _("%(done)s class(es) loaded, %(skipped)s line(s) not.", done=done, skipped=len(notes)) + (
            "\n" + "\n".join(notes) if notes else "")
        return self._reopen()

    def _reopen(self):
        return {"type": "ir.actions.act_window", "res_model": self._name, "res_id": self.id, "view_mode": "form",
                "target": "new"}
