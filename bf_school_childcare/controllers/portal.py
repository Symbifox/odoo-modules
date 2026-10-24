import re
from datetime import timedelta

from odoo import _, fields, http
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.http import request

from odoo.addons.portal.controllers.portal import CustomerPortal

from ..models.common import DAY_FIELDS

#: What a family sees ahead, and back.
DAYS_AHEAD = 14
HISTORY_DAYS = 30
ERRORS = (UserError, ValidationError, AccessError)


class SchoolChildcarePortal(CustomerPortal):
    """Child care on the family portal.

    🔴 Only the adults with parental authority: booking, absences and who may pick up the child
    commit the child. Every id received from a form is checked against those children before
    anything is read in sudo; the model methods check the link again.
    """

    def _childcare_children(self):
        partner = request.env.user.partner_id
        return partner, partner._school_authority_links().student_id

    @staticmethod
    def _child(children, student_id):
        student = children.filtered(lambda c: str(c.id) == str(student_id or ""))
        if not student:
            raise request.not_found()
        return student

    @staticmethod
    def _date(value):
        try:
            return fields.Date.to_date(value) if value else None
        except ValueError:
            return None

    @staticmethod
    def _hour(value):
        match = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", value or "")
        if not match or int(match.group(1)) > 23 or int(match.group(2)) > 59:
            return None
        return int(match.group(1)) + int(match.group(2)) / 60.0

    def _back(self, message=None, error=False):
        request.session["school_childcare_message"] = message
        request.session["school_childcare_error"] = bool(error)
        return request.redirect("/my/school/childcare")

    def _slot(self, student, slot_id):
        """A period of the child's school, or nothing."""
        try:
            slot_id = int(slot_id or 0)
        except (TypeError, ValueError):
            raise request.not_found() from None
        slot = request.env["bf.school.childcare.slot"].sudo().browse(slot_id).exists()
        school = student._childcare_school(student._childcare_local_today())
        if not slot or slot.school_id != school:
            raise request.not_found()
        return slot

    # --- The page -------------------------------------------------------------------------------

    def _childcare_card(self, child):
        env = request.env
        today = child._childcare_local_today()
        school = child._childcare_school(today)
        card = {"student": child, "school": school}
        if not school:
            return card
        Slot, Booking = env["bf.school.childcare.slot"].sudo(), env["bf.school.childcare.booking"].sudo()
        Absence = env["bf.school.childcare.absence"].sudo()
        slots = Slot.search([("school_id", "=", school.id)])
        now = fields.Datetime.now()
        notice = timedelta(hours=school.childcare_booking_notice_hours)
        days = []
        for offset in range(DAYS_AHEAD):
            day = today + timedelta(days=offset)
            if day.weekday() >= 5:
                continue
            entries = []
            absences = Absence.search([("student_id", "=", child.id), ("date", "=", day)])
            for slot in slots:
                if not slot._runs_on(day) or not slot._accepts(child, day):
                    continue
                registered = child in slot._school_registered(day).registration_id.student_id
                booking = Booking.search([("student_id", "=", child.id), ("slot_id", "=", slot.id),
                                          ("date", "=", day), ("state", "=", "booked")], limit=1)
                in_time = now + notice <= slot._school_start(day)
                room = slot._school_has_room(day)
                entries.append({
                    "slot": slot, "registered": registered, "booking": booking,
                    "absent": any(a._school_covers(day, slot) for a in absences),
                    "activity": slot._school_activity(day),
                    "can_book": not registered and not booking and in_time and room,
                    "full": not registered and not booking and not room,
                    "too_late": not registered and not booking and room and not in_time,
                    "can_cancel": bool(booking) and now < slot._school_start(day),
                })
            days.append({"date": day, "entries": entries, "closed": not school._calendar_childcare_open(day)})
        Registration = env["bf.school.childcare.registration"].sudo()
        card.update({
            "today": today,
            "tz": school._calendar_tz().zone,
            "days": days,
            "slots": slots,
            "registrations": Registration.search([("student_id", "=", child.id), ("state", "in", ("draft", "active")),
                                                  ("date_end", ">=", today)]),
            "guardians": child.sudo().student_guardian_link_ids.filtered("can_pickup").guardian_id,
            "persons": env["bf.school.childcare.pickup.person"].sudo().search(
                [("student_id", "=", child.id), ("state", "=", "active")]),
            "walks": env["bf.school.childcare.walk_alone"].sudo().search(
                [("student_id", "=", child.id), ("state", "=", "active")]),
            "departures": env["bf.school.childcare.sheet.line"].sudo().search(
                [("student_id", "=", child.id), ("departed_on", ">=", now - timedelta(days=HISTORY_DAYS))],
                order="departed_on desc", limit=60),
        })
        return card

    @http.route("/my/school/childcare", type="http", auth="user", website=True)
    def portal_school_childcare(self, **kw):
        _partner, children = self._childcare_children()
        values = self._prepare_portal_layout_values()
        values.update({
            "page_name": "school_childcare",
            "cards": [self._childcare_card(child) for child in children.sorted("name")],
            "day_fields": DAY_FIELDS,
            "day_labels": {"mon": _("Mon."), "tue": _("Tue."), "wed": _("Wed."), "thu": _("Thu."), "fri": _("Fri.")},
            "message": request.session.pop("school_childcare_message", None),
            "error": request.session.pop("school_childcare_error", False),
        })
        return request.render("bf_school_childcare.portal_school_childcare", values)

    # --- Bookings and absences ------------------------------------------------------------------

    @http.route("/my/school/childcare/book", type="http", auth="user", methods=["POST"], website=True)
    def portal_school_childcare_book(self, student_id=None, slot_id=None, date=None, **kw):
        partner, children = self._childcare_children()
        student = self._child(children, student_id)
        slot, day = self._slot(student, slot_id), self._date(date)
        if not day:
            raise request.not_found()
        try:
            request.env["bf.school.childcare.booking"]._school_book_by(partner, student, slot, day)
        except ERRORS as error:
            return self._back(str(error.args[0]), error=True)
        return self._back(_("Booked."))

    @http.route("/my/school/childcare/booking/<int:booking_id>/cancel", type="http", auth="user",
                methods=["POST"], website=True)
    def portal_school_childcare_cancel(self, booking_id, **kw):
        partner, children = self._childcare_children()
        booking = request.env["bf.school.childcare.booking"].sudo().browse(booking_id).exists()
        if not booking or booking.student_id not in children:
            raise request.not_found()
        try:
            booking._school_cancel_by(partner)
        except ERRORS as error:
            return self._back(str(error.args[0]), error=True)
        if booking.cancel_late:
            return self._back(_("Cancelled. It was late: the place stays due."))
        return self._back(_("Cancelled."))

    @http.route("/my/school/childcare/absence", type="http", auth="user", methods=["POST"], website=True)
    def portal_school_childcare_absence(self, student_id=None, date=None, slot_id=None, reason=None, **kw):
        partner, children = self._childcare_children()
        student = self._child(children, student_id)
        slot = self._slot(student, slot_id) if slot_id else None
        day = self._date(date)
        if not day:
            return self._back(_("The date is not valid."), error=True)
        try:
            request.env["bf.school.childcare.absence"]._school_declare_by(partner, student, day, slot, reason)
        except ERRORS as error:
            return self._back(str(error.args[0]), error=True)
        return self._back(_("Thank you: the absence is noted. A place booked or registered stays due."))

    @http.route("/my/school/childcare/register", type="http", auth="user", methods=["POST"], website=True)
    def portal_school_childcare_register(self, student_id=None, date_start=None, **kw):
        partner, children = self._childcare_children()
        student = self._child(children, student_id)
        day = self._date(date_start)
        if not day:
            return self._back(_("The date is not valid."), error=True)
        choices = {}
        for key in kw:
            match = re.fullmatch(r"slot_(\d+)_(mon|tue|wed|thu|fri)", key)
            if match:
                slot = self._slot(student, match.group(1))
                choices.setdefault(slot, []).append(match.group(2))
        try:
            request.env["bf.school.childcare.registration"]._school_request_by(partner, student, day, choices)
        except ERRORS as error:
            return self._back(str(error.args[0]), error=True)
        return self._back(_("Thank you: the request is sent. The school confirms it by email."))

    # --- Who may pick up the child -----------------------------------------------------------------

    @http.route("/my/school/childcare/person/add", type="http", auth="user", methods=["POST"], website=True)
    def portal_school_childcare_person_add(self, student_id=None, name=None, relationship=None, phone=None,
                                           date_from=None, date_to=None, **kw):
        partner, children = self._childcare_children()
        student = self._child(children, student_id)
        start, end = self._date(date_from), self._date(date_to)
        if not start or (date_to and not end):
            return self._back(_("The dates are not valid."), error=True)
        try:
            request.env["bf.school.childcare.pickup.person"]._school_add_by(
                partner, student, name, relationship, phone, start, end)
        except ERRORS as error:
            return self._back(str(error.args[0]), error=True)
        return self._back(_("Added. Every adult with parental authority is told."))

    @http.route("/my/school/childcare/person/<int:person_id>/remove", type="http", auth="user",
                methods=["POST"], website=True)
    def portal_school_childcare_person_remove(self, person_id, **kw):
        partner, children = self._childcare_children()
        person = request.env["bf.school.childcare.pickup.person"].sudo().browse(person_id).exists()
        if not person or person.student_id not in children:
            raise request.not_found()
        try:
            person._school_remove_by(partner)
        except ERRORS as error:
            return self._back(str(error.args[0]), error=True)
        return self._back(_("Removed. Every adult with parental authority is told."))

    @http.route("/my/school/childcare/walk_alone/sign", type="http", auth="user", methods=["POST"], website=True)
    def portal_school_childcare_walk_sign(self, student_id=None, from_time=None, date_from=None, date_to=None,
                                          accept=None, **kw):
        partner, children = self._childcare_children()
        student = self._child(children, student_id)
        hour, start, end = self._hour(from_time), self._date(date_from), self._date(date_to)
        days = [day for day in DAY_FIELDS if kw.get("day_%s" % day)]
        if hour is None or not start or (date_to and not end):
            return self._back(_("The time or the dates are not valid."), error=True)
        if not accept:
            return self._back(_("Tick the box to sign."), error=True)
        try:
            request.env["bf.school.childcare.walk_alone"]._school_sign_by(partner, student, days, hour, start, end,
                                                                          lang=request.env.lang)
        except ERRORS as error:
            return self._back(str(error.args[0]), error=True)
        return self._back(_("Signed. Every adult with parental authority is told."))

    @http.route("/my/school/childcare/walk_alone/<int:walk_id>/revoke", type="http", auth="user",
                methods=["POST"], website=True)
    def portal_school_childcare_walk_revoke(self, walk_id, **kw):
        partner, children = self._childcare_children()
        walk = request.env["bf.school.childcare.walk_alone"].sudo().browse(walk_id).exists()
        if not walk or walk.student_id not in children:
            raise request.not_found()
        try:
            walk._school_revoke_by(partner)
        except ERRORS as error:
            return self._back(str(error.args[0]), error=True)
        return self._back(_("Revoked. Every adult with parental authority is told."))
