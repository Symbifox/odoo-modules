from datetime import datetime, timedelta

import psycopg2
import pytz

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools import format_date, mute_logger

from odoo.addons.bf_school_calendar.models.calendar_day import DEFAULT_TZ

from .common import time_label
from .slot import TO_CLASS_KINDS

SHEET_SYSTEM_DEFAULTS = {"state": "open", "closed_on": False}
#: What a line is born with; the buttons of the sheet move them.
LINE_SYSTEM_DEFAULTS = {"origin": "walk_in", "presence": "expected", "arrived_on": False, "departed_on": False,
                        "departure_kind": False, "departure_guardian_id": False, "departure_person_id": False,
                        "departure_note": False, "late_minutes": 0, "registration_id": False, "booking_id": False}
DEPARTURE_KINDS = [
    ("guardian", "With a parent or guardian"),
    ("authorized", "With an authorized person"),
    ("walk_alone", "Alone (signed permission)"),
    ("class", "To class"),
    ("other", "With someone else"),
]
MANAGER = "bf_school_childcare.group_school_childcare_manager"


class ChildcareSheet(models.Model):
    """One period of child care on one date: who is expected, who came, who left with whom."""

    _name = "bf.school.childcare.sheet"
    _inherit = ["mail.thread", "mail.activity.mixin", "bf.school.childcare.guarded"]
    _description = "Child care daily sheet"
    _order = "date desc, slot_id"
    _school_system_defaults = SHEET_SYSTEM_DEFAULTS

    slot_id = fields.Many2one("bf.school.childcare.slot", "Period", required=True, ondelete="restrict", index=True)
    slot_kind = fields.Selection(related="slot_id.kind", string="Kind of period")
    school_id = fields.Many2one(related="slot_id.school_id", store=True, index=True)
    company_id = fields.Many2one(related="slot_id.company_id", store=True, index=True)
    date = fields.Date(required=True, index=True, default=lambda s: s._default_date())
    state = fields.Selection([("open", "Open"), ("closed", "Closed")], default="open", required=True,
                             readonly=True, tracking=True)
    user_id = fields.Many2one("res.users", "Opened by", readonly=True, default=lambda s: s.env.user)
    closed_on = fields.Datetime(readonly=True)
    line_ids = fields.One2many("bf.school.childcare.sheet.line", "sheet_id", "Children")
    expected_count = fields.Integer("Expected", compute="_compute_counts")
    present_count = fields.Integer("Still here", compute="_compute_counts")
    absent_count = fields.Integer("Absent", compute="_compute_counts")
    departed_count = fields.Integer("Departures", compute="_compute_counts")

    _sql_constraints = [("one_sheet", "UNIQUE(slot_id, date)", "A period has one sheet per date.")]

    @api.model
    def _default_date(self):
        """Today on the school's clock (the company's time zone), never the server's UTC."""
        tz = pytz.timezone(self.env.company.partner_id.tz or DEFAULT_TZ)
        return datetime.now(pytz.utc).astimezone(tz).date()

    @api.depends("slot_id", "date")
    @api.depends_context("lang")
    def _compute_display_name(self):
        for sheet in self:
            sheet.display_name = " · ".join(filter(None, [
                sheet.slot_id.name, format_date(self.env, sheet.date) if sheet.date else ""]))

    @api.depends("line_ids.presence", "line_ids.departed_on")
    def _compute_counts(self):
        for sheet in self:
            lines = sheet.line_ids
            sheet.expected_count = len(lines.filtered(lambda l: l.presence == "expected"))
            sheet.present_count = len(lines.filtered(lambda l: l.presence == "present" and not l.departed_on))
            sheet.absent_count = len(lines.filtered(lambda l: l.presence == "absent"))
            sheet.departed_count = len(lines.filtered("departed_on"))

    @api.model_create_multi
    def create(self, vals_list):
        self._school_guard_create(vals_list)
        Slot = self.env["bf.school.childcare.slot"]
        for vals in vals_list:
            if not self.env.su:
                # Who opened the sheet is the person opening it, never someone else.
                if vals.get("user_id") and vals["user_id"] != self.env.uid:
                    raise UserError(_("These fields are set by the school's process, not by hand: %s.",
                                      self._fields["user_id"].string))
                vals["user_id"] = self.env.uid
            slot = Slot.browse(vals.get("slot_id"))
            day = fields.Date.to_date(vals.get("date")) or self._default_date()
            vals["date"] = day
            if slot and not slot.sudo()._runs_on(day):
                raise UserError(_("%(slot)s does not run on %(date)s.", slot=slot.sudo().display_name,
                                  date=format_date(self.env, day)))
            if slot and not self.env.su and not self.env.user.has_group(MANAGER) \
                    and day != slot.sudo().school_id._calendar_today():
                raise UserError(_("The educator opens today's sheets only."))
        sheets = super().create(vals_list)
        for sheet in sheets:
            sheet._school_fill()
        return sheets

    def write(self, vals):
        self._school_guard_write(vals)
        if not self.env.su and {"slot_id", "date", "user_id"} & set(vals):
            raise UserError(_("A sheet is not moved to another period or date."))
        return super().write(vals)

    def unlink(self):
        if not self.env.su and self.line_ids.filtered("arrived_on"):
            raise UserError(_("A sheet with arrivals is kept: it is the record of who came."))
        return super().unlink()

    # --- Who is expected --------------------------------------------------------------------

    def _school_expected(self):
        """{student: (origin, registration, booking)} for this period on this date."""
        self.ensure_one()
        Registration = self.env["bf.school.childcare.registration"].sudo()
        Booking = self.env["bf.school.childcare.booking"].sudo()
        expected = {}
        for line in self.slot_id._school_registered(self.date):
            student = line.registration_id.student_id
            # A child who left the school keeps a registration until the office ends it: not expected.
            if student._childcare_levels(self.school_id, self.date):
                expected.setdefault(student, ("registration", line.registration_id, Booking))
        for booking in self.slot_id._school_booked(self.date):
            expected.setdefault(booking.student_id, ("booking", Registration, booking))
        return expected

    def _school_absent(self, students):
        """Those of `students` with an absence notice for this period or the day, or a whole-day
        absence declared to the school."""
        self.ensure_one()
        absences = self.env["bf.school.childcare.absence"].sudo().search([
            ("student_id", "in", students.ids), ("date", "=", self.date)]).filtered(
            lambda a: a._school_covers(self.date, self.slot_id))
        declarations = self.env["bf.school.absence.declaration"].sudo().search([
            ("student_id", "in", students.ids), ("part", "=", "full"),
            ("date_from", "<=", self.date), ("date_to", ">=", self.date)])
        return absences.student_id | declarations.student_id

    def _school_fill(self):
        """Add the children expected and not on the sheet yet. The others are not touched."""
        self.ensure_one()
        on_sheet = self.sudo().line_ids.student_id
        expected = {student: value for student, value in self._school_expected().items() if student not in on_sheet}
        if not expected:
            return 0
        Partner = self.env["res.partner"].sudo()
        absent = self._school_absent(Partner.union(*expected))
        self.env["bf.school.childcare.sheet.line"].sudo().create([{
            "sheet_id": self.id, "student_id": student.id, "origin": origin,
            "registration_id": registration.id, "booking_id": booking.id,
            "presence": "absent" if student in absent else "expected",
        } for student, (origin, registration, booking) in expected.items()])
        return len(expected)

    # --- Buttons ----------------------------------------------------------------------------

    def action_refresh(self):
        """New bookings and registrations since the sheet was opened; nothing else changes."""
        added = 0
        for sheet in self:
            sheet.check_access("write")
            if sheet.state != "open":
                raise UserError(_("This sheet is closed."))
            added += sheet._school_fill()
        return {
            "type": "ir.actions.client", "tag": "display_notification",
            "params": {"message": _("%s child(ren) added.", added), "type": "info", "sticky": False,
                       "next": {"type": "ir.actions.client", "tag": "soft_reload"}},
        }

    def action_close(self):
        """Every child has left. Before school and at lunch, the children still here go to class."""
        now = fields.Datetime.now()
        for sheet in self:
            sheet.check_access("write")
            if sheet.state != "open":
                raise UserError(_("This sheet is already closed."))
            if sheet.date > sheet.sudo().school_id._calendar_today():
                raise UserError(_("This sheet is for a later date: it closes on its day or after."))
            lines = sheet.sudo().line_ids
            if sheet.slot_id.kind in TO_CLASS_KINDS:
                lines.filtered(lambda l: l.presence == "present" and not l.departed_on).write(
                    {"departed_on": now, "departure_kind": "class"})
            missing = lines.filtered(lambda l: l.presence == "present" and not l.departed_on)
            if missing:
                raise UserError(_("Record the departure of every child first: %s.",
                                  ", ".join(missing.student_id.mapped("name"))))
            lines.filtered(lambda l: l.presence == "expected").write({"presence": "absent"})
            sheet.sudo().write({"state": "closed", "closed_on": now})
            sheet._school_on_closed()
        # 🔴 The educator reads open sheets only: the closed one would answer "missing record".
        return self._action_school_today()

    def action_reopen(self):
        if not (self.env.su or self.env.user.has_group(MANAGER)):
            raise AccessError(_("Only the child care manager reopens a sheet."))
        self.check_access("write")
        self.sudo().write({"state": "open", "closed_on": False})
        return True

    def _school_on_closed(self):
        """Extension point: a sheet was closed (billing modules count the day here)."""
        return True

    def _school_check_day(self, departure=False):
        """🔴 Arrivals and departures are recorded on today's sheet: the rules of the day (a
        restriction in force since this morning, an authorized person who starts Monday) and the
        lateness are read on the sheet's date. A future sheet takes nothing; a past one left open
        is corrected by the manager only."""
        for sheet in self:
            today = sheet.sudo().school_id._calendar_today()
            if sheet.date > today:
                raise UserError(_("This sheet is for a later date: arrivals and departures are recorded on the day."))
            # 🔴 A past sheet takes no arrival nor new child, even from the manager: the rules of
            # that day would decide what happens today. Only the departure of a child who came is
            # corrected, by the manager.
            if sheet.date < today and not (self.env.su or (departure and self.env.user.has_group(MANAGER))):
                raise UserError(_("This sheet is from a past day: the child care manager corrects it."))

    def _school_review_other(self, line):
        """A child left with someone not on file: the manager reviews it."""
        self.ensure_one()
        sheet = self.sudo()
        body = _("%(student)s left with someone not on file: %(note)s", student=line.student_id.name,
                 note=line.departure_note)
        sheet.message_post(body=body)
        responsible = sheet.school_id._childcare_responsible()
        if responsible:
            sheet.activity_schedule("mail.mail_activity_data_todo", user_id=responsible.id,
                                    summary=_("Departure to review"), note=body)

    @api.model
    def _action_school_today(self):
        """Today's sheets of every period that runs today, opened when they are not yet."""
        self.browse().check_access("create")
        sheets = self.browse()
        for slot in self.env["bf.school.childcare.slot"].search([]):
            today = slot.school_id._calendar_today()
            if not slot._runs_on(today):
                continue
            sheet = self.sudo().search([("slot_id", "=", slot.id), ("date", "=", today)])
            if not sheet:
                try:
                    with self.env.cr.savepoint(), mute_logger("odoo.sql_db"):
                        sheet = self.create({"slot_id": slot.id, "date": today})
                except psycopg2.IntegrityError:  # opened at the same moment by a colleague
                    sheet = self.sudo().search([("slot_id", "=", slot.id), ("date", "=", today)])
            sheets |= sheet.filtered(lambda s: s.state == "open")
        action = self.env["ir.actions.act_window"]._for_xml_id("bf_school_childcare.bf_school_childcare_sheet_action")
        action.update({"domain": [("id", "in", sheets.ids)], "context": {}, "name": _("Today")})
        return action


class ChildcareSheetLine(models.Model):
    """A child on a daily sheet."""

    _name = "bf.school.childcare.sheet.line"
    _inherit = ["bf.school.childcare.guarded"]
    _description = "Child on a child care sheet"
    _order = "sheet_id, id"
    _rec_names_search = ["student_id"]
    _school_system_defaults = LINE_SYSTEM_DEFAULTS

    sheet_id = fields.Many2one("bf.school.childcare.sheet", required=True, ondelete="cascade", index=True)
    date = fields.Date(related="sheet_id.date", store=True, index=True)
    slot_id = fields.Many2one(related="sheet_id.slot_id", store=True, index=True)
    school_id = fields.Many2one(related="sheet_id.school_id", store=True)
    company_id = fields.Many2one(related="sheet_id.company_id", store=True)
    student_id = fields.Many2one("res.partner", "Student", required=True, ondelete="restrict", index=True,
                                 domain=[("is_student", "=", True)])
    origin = fields.Selection([("registration", "Regular registration"), ("booking", "Occasional booking"),
                               ("walk_in", "Not registered")],
                              default="walk_in", required=True, readonly=True)
    # The manager's: the educator reads the child, not the family's paperwork.
    registration_id = fields.Many2one("bf.school.childcare.registration", readonly=True, ondelete="set null",
                                      groups=MANAGER)
    booking_id = fields.Many2one("bf.school.childcare.booking", readonly=True, ondelete="set null", groups=MANAGER)
    presence = fields.Selection([("expected", "Expected"), ("present", "Present"), ("absent", "Absent")],
                                default="expected", required=True, readonly=True)
    arrived_on = fields.Datetime(readonly=True)
    departed_on = fields.Datetime(readonly=True)
    arrived_time = fields.Char("Arrived at", compute="_compute_times")
    departed_time = fields.Char("Left at", compute="_compute_times")
    departure_kind = fields.Selection(DEPARTURE_KINDS, "Departure", readonly=True)
    departure_guardian_id = fields.Many2one("res.partner", "Parent or guardian", readonly=True, ondelete="restrict")
    departure_person_id = fields.Many2one("bf.school.childcare.pickup.person", "Authorized person", readonly=True,
                                          ondelete="restrict", groups=MANAGER)
    departure_note = fields.Char("Who came", readonly=True)
    departure_label = fields.Char("Left with", compute="_compute_departure_label")
    late_minutes = fields.Integer("Minutes late", readonly=True,
                                  help="After the end of the period and its grace minutes, school's clock.")
    note = fields.Char(help="Internal. Never shown to the family.")
    # Computed in sudo for the educator, who reads neither the family links nor the restrictions.
    alert = fields.Char("Alert", compute="_compute_child_info")
    has_restriction = fields.Boolean("Restriction", compute="_compute_child_info")
    restriction_names = fields.Char("Must not pick up", compute="_compute_child_info")
    permitted_summary = fields.Char("May pick up", compute="_compute_child_info")

    _sql_constraints = [("one_line", "UNIQUE(sheet_id, student_id)", "A child appears once on a sheet.")]

    @api.depends("student_id", "sheet_id")
    @api.depends_context("lang")
    def _compute_display_name(self):
        for line in self:
            line.display_name = " · ".join(filter(None, [line.student_id.name, line.sheet_id.display_name]))

    def _compute_times(self):
        for line in self:
            school = line.sudo().school_id
            line.arrived_time = school._calendar_local(line.arrived_on).strftime("%H:%M") \
                if line.arrived_on and school else ""
            line.departed_time = school._calendar_local(line.departed_on).strftime("%H:%M") \
                if line.departed_on and school else ""

    @api.depends_context("lang")
    def _compute_departure_label(self):
        kinds = dict(self._fields["departure_kind"]._description_selection(self.env))
        for line in self:
            done = line.sudo()
            label = kinds.get(done.departure_kind) or ""
            if done.departure_kind == "guardian":
                label = done.departure_guardian_id.name
            elif done.departure_kind == "authorized":
                label = done.departure_person_id.display_name
            elif done.departure_kind == "other":
                label = "%s: %s" % (label, done.departure_note) if done.departure_note else label
            line.departure_label = label or ""

    @api.depends_context("lang")
    def _compute_child_info(self):
        for line in self:
            done = line.sudo()
            student = done.student_id
            alert = []
            if student and "health_alert" in student._fields and student.health_alert:
                alert.append(student.health_alert)
            if student and "meal_allergen_ids" in student._fields and student.meal_allergen_ids:
                alert.append(_("Allergies: %s", ", ".join(student.meal_allergen_ids.mapped("name"))))
            line.alert = " · ".join(alert)
            restrictions = student._childcare_restrictions(done.date) if student and done.date else False
            line.has_restriction = bool(restrictions)
            line.restriction_names = ", ".join(restrictions.mapped("name")) if restrictions else ""
            line.permitted_summary = "; ".join(o["label"] for o in done._school_permitted_options(summary=True)) \
                if student and done.date else ""

    # --- Rules ------------------------------------------------------------------------------

    @api.model_create_multi
    def create(self, vals_list):
        self._school_guard_create(vals_list)
        if not self.env.su:
            Sheet, Partner = self.env["bf.school.childcare.sheet"], self.env["res.partner"].sudo()
            now = fields.Datetime.now()
            for vals in vals_list:
                sheet = Sheet.browse(vals.get("sheet_id"))
                student = Partner.browse(vals.get("student_id"))
                # The sheet is read with the user's rights: the educator reaches open sheets only.
                if not sheet.exists() or sheet.state != "open":
                    raise UserError(_("A child is added to an open sheet."))
                sheet._school_check_day()
                if not student or not student._childcare_levels(sheet.school_id, sheet.date):
                    raise UserError(_("Child care is for the preschool and elementary students of the school."))
                # 🔴 A child present without registration: an occasional presence, there now.
                origin, registration, booking = sheet._school_expected().get(
                    student, ("walk_in", self.env["bf.school.childcare.registration"], self.env["bf.school.childcare.booking"]))
                vals.update(origin=origin, registration_id=registration.id, booking_id=booking.id,
                            presence="present", arrived_on=now)
        lines = super().create(vals_list)
        if not self.env.su:
            for line in lines.filtered(lambda l: l.origin == "walk_in"):
                line.sheet_id.sudo().message_post(body=_("%s is here without registration nor booking.",
                                                         line.student_id.name))
            lines._school_on_walk_in()
        return lines

    def write(self, vals):
        self._school_guard_write(vals)
        if not self.env.su:
            if {"student_id", "sheet_id"} & set(vals):
                raise UserError(_("A line is not moved: add the child again."))
            if any(line.sheet_id.state != "open" for line in self):
                raise UserError(_("This sheet is closed."))
        return super().write(vals)

    def unlink(self):
        if not self.env.su and any(line.origin != "walk_in" or line.departed_on or line.sheet_id.state != "open"
                                   for line in self):
            raise UserError(_("Only a child added by mistake, not gone yet, is removed from an open sheet."))
        return super().unlink()

    # --- Buttons ----------------------------------------------------------------------------

    def _school_check_open(self, departure=False):
        self.check_access("write")
        if any(line.sheet_id.state != "open" for line in self):
            raise UserError(_("This sheet is closed."))
        self.sheet_id._school_check_day(departure=departure)

    def _school_after_the_fact(self):
        """A past sheet corrected by the manager: the time of the click is not the time the child
        left, so no lateness is counted."""
        self.ensure_one()
        return self.date < self.sudo().school_id._calendar_today()

    def action_arrive(self):
        self._school_check_open()
        now = fields.Datetime.now()
        for line in self:
            if line.arrived_on:
                raise UserError(_("The arrival of %s is already recorded.", line.student_id.name))
            line.sudo().write({"presence": "present", "arrived_on": now})
        return True

    def action_absent(self):
        self._school_check_open()
        for line in self:
            if line.arrived_on:
                raise UserError(_("%s arrived: record the departure instead.", line.student_id.name))
            line.sudo().write({"presence": "absent"})
        return True

    def action_remove(self):
        """A child added by mistake (not registered, not gone): the educator removes the line."""
        self._school_check_open()
        if any(line.origin != "walk_in" or line.departed_on for line in self):
            raise UserError(_("Only a child added by mistake, not gone yet, is removed from an open sheet."))
        for line in self:
            line.sheet_id.sudo().message_post(body=_("%(student)s removed from the sheet by %(user)s (added by mistake).",
                                                     student=line.student_id.name, user=self.env.user.name))
        self.sudo()._school_on_removed()
        self.sudo().unlink()
        return True

    def _school_on_removed(self):
        """Extension point: lines added by mistake are about to be removed (billing modules drop
        what `_school_on_walk_in` started)."""
        return True

    def action_to_class(self):
        for line in self:
            line._school_depart("class")
        return True

    def action_departure(self):
        """The educator chooses who takes the child, among those allowed today."""
        self.ensure_one()
        self._school_check_open(departure=True)
        self._school_check_can_leave()
        wizard = self.env["bf.school.childcare.departure"].create({"line_id": self.id})
        return {"type": "ir.actions.act_window", "res_model": "bf.school.childcare.departure", "res_id": wizard.id,
                "view_mode": "form", "views": [(False, "form")], "target": "new",
                "name": _("Departure of %s", self.student_id.name)}

    def _school_check_can_leave(self):
        for line in self:
            if line.presence != "present" or not line.arrived_on:
                raise UserError(_("Record the arrival of %s first.", line.student_id.name))
            if line.departed_on:
                raise UserError(_("The departure of %s is already recorded.", line.student_id.name))

    # --- Departures -------------------------------------------------------------------------

    def _school_local_hour(self, value):
        self.ensure_one()
        local = self.sudo().school_id._calendar_local(value)
        return local.hour + local.minute / 60.0

    def _school_late_minutes(self, departed_on):
        """Minutes after the end of the period and its grace, on the school's clock.

        🔴 The end is placed on the school's clock first (a period ending at 20:30 in Montréal
        ends at 00:30 UTC the next day), then compared with the departure in UTC.
        """
        self.ensure_one()
        slot = self.sudo().slot_id
        end = slot._school_end(self.date) + timedelta(minutes=slot.grace_minutes)
        return max(0, int((departed_on - end).total_seconds() // 60))

    def _school_permitted_options(self, summary=False):
        """Who may take this child today, computed in sudo (the educator reads neither the family
        links nor the restrictions). A restricted person is never offered.

        `summary`: also list a permission to leave alone that starts later today.
        """
        self.ensure_one()
        line = self.sudo()
        student, day = line.student_id, line.date
        restrictions = student._childcare_restrictions(day)
        relationships = dict(self.env["bf.school.guardian.link"]._fields["relationship"]._description_selection(self.env))
        options = []
        for link in student.student_guardian_link_ids.filtered("can_pickup"):
            if restrictions._school_matches(partner=link.guardian_id) or restrictions._school_named_in(
                    link.guardian_id.name):
                continue
            options.append({"kind": "guardian", "partner": link.guardian_id, "person": False,
                            "needs_id_check": False,
                            "label": "%s (%s)" % (link.guardian_id.name, relationships.get(link.relationship, ""))})
        persons = self.env["bf.school.childcare.pickup.person"].sudo().search(
            [("student_id", "=", student.id), ("state", "=", "active")]).filtered(lambda p: p._is_valid_on(day))
        for person in persons:
            if restrictions._school_matches(name=person.name) or restrictions._school_named_in(person.name):
                continue
            label = person.display_name if person.id_checked_on else _("%s, ID to check", person.display_name)
            options.append({"kind": "authorized", "partner": False, "person": person,
                            "needs_id_check": not person.id_checked_on, "label": label})
        hour = line._school_local_hour(fields.Datetime.now())
        walks = self.env["bf.school.childcare.walk_alone"].sudo().search(
            [("student_id", "=", student.id), ("state", "=", "active")])
        for walk in walks.sorted("from_time"):
            if walk._school_permits(day, hour, line.school_id) or (summary and walk._school_permits(day, 24, line.school_id)):
                options.append({"kind": "walk_alone", "partner": False, "person": False, "needs_id_check": False,
                                "label": _("Alone, from %s", time_label(walk.from_time))})
                break
        return options

    def _school_depart(self, kind, partner=None, person=None, note=None, id_checked=False):
        """The child leaves. Every check lives here; the departure window only calls it.

        🔴 Refused: a person under a pick-up restriction (by contact or by name, also written in
        the note of "someone else"), an adult whose link does not allow picking up, an
        authorized person outside their dates or revoked, an authorized person whose ID was
        never checked, leaving alone outside the days and hours signed.
        """
        self.ensure_one()
        self._school_check_open(departure=True)
        self._school_check_can_leave()
        line = self.sudo()
        student, day, slot = line.student_id, line.date, line.slot_id
        restrictions = student._childcare_restrictions(day)
        refused = _("%(person)s may not pick up %(student)s. Keep the child and call the child care manager.")
        now = fields.Datetime.now()
        vals = {"departed_on": now, "departure_kind": kind}
        if kind == "guardian":
            partner = (partner or self.env["res.partner"]).sudo()
            if restrictions._school_matches(partner=partner) or restrictions._school_named_in(partner.name):
                raise UserError(refused % {"person": partner.name, "student": student.name})
            if not partner or partner not in student.student_guardian_link_ids.filtered("can_pickup").guardian_id:
                raise UserError(_("%(adult)s may not pick up %(student)s.", adult=partner.name or "?",
                                  student=student.name))
            vals["departure_guardian_id"] = partner.id
        elif kind == "authorized":
            person = (person or self.env["bf.school.childcare.pickup.person"]).sudo()
            if person and (restrictions._school_matches(name=person.name)
                           or restrictions._school_named_in(person.name)):
                raise UserError(refused % {"person": person.name, "student": student.name})
            if not person or person.student_id != student or not person._is_valid_on(day):
                raise UserError(_("This person may not pick up %s today.", student.name))
            if not person.id_checked_on:
                if not id_checked:
                    raise UserError(_("Check the ID of %s first, then tick the box.", person.name))
                person.write({"id_checked_on": now, "id_checked_by_id": self.env.uid})
            vals["departure_person_id"] = person.id
        elif kind == "walk_alone":
            # Corrected afterwards: the hour of the click is not the hour the child left.
            hour = 24.0 if line._school_after_the_fact() else line._school_local_hour(now)
            walks = self.env["bf.school.childcare.walk_alone"].sudo().search(
                [("student_id", "=", student.id), ("state", "=", "active")])
            if not any(walk._school_permits(day, hour, line.school_id) for walk in walks):
                raise UserError(_("%s may not leave alone today at this time.", student.name))
        elif kind == "class":
            if slot.kind not in TO_CLASS_KINDS:
                raise UserError(_("Children leave this period with an adult, not to class."))
        elif kind == "other":
            note = (note or "").strip()
            if not note:
                raise UserError(_("Write who came and why."))
            if restrictions._school_named_in(note):
                raise UserError(refused % {"person": note, "student": student.name})
            vals["departure_note"] = note[:200]
        else:
            raise UserError(_("Choose who takes the child."))
        after_the_fact = line._school_after_the_fact()
        if kind != "class" and not after_the_fact:
            vals["late_minutes"] = line._school_late_minutes(now)
        line.write(vals)
        if after_the_fact:
            line.sheet_id.message_post(body=_("Departure of %(student)s recorded afterwards by %(user)s.",
                                              student=student.name, user=self.env.user.name))
        if kind == "other":
            line.sheet_id._school_review_other(line)
        line._school_on_departure()
        return True

    def _school_on_departure(self):
        """Extension point: a child left (billing modules read `late_minutes` here)."""
        return True

    def _school_on_walk_in(self):
        """Extension point: a child came without registration nor booking."""
        return True
