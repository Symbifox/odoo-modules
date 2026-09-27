from odoo import http
from odoo.exceptions import UserError
from odoo.http import request

from odoo.addons.portal.controllers.portal import CustomerPortal


class SchoolMeetingPortal(CustomerPortal):

    def _prepare_home_portal_values(self, counters):
        values = super()._prepare_home_portal_values(counters)
        if "school_meeting_count" in counters:
            values["school_meeting_count"] = len(
                {o[0] for o in request.env["bf.school.meeting.session"]._school_offers_for(
                    request.env.user.partner_id)})
        return values

    @http.route("/my/school/meetings", type="http", auth="user", website=True)
    def portal_school_meetings(self, **kw):
        partner = request.env.user.partner_id
        # 🔴 The message travels in the session, never in the URL: a link carrying a text
        # would display anything anyone wrote on a page of the school.
        error = request.session.pop("school_meeting_error", None)
        offers = request.env["bf.school.meeting.session"]._school_offers_for(partner)
        Slot = request.env["bf.school.meeting.slot"].sudo()
        rows = []
        for session, student, teacher in offers:
            booked = Slot.search([("session_id", "=", session.id), ("teacher_id", "=", teacher.id),
                                  ("student_id", "=", student.id)], limit=1)
            free = Slot.search([("session_id", "=", session.id), ("teacher_id", "=", teacher.id),
                                ("student_id", "=", False)]) if not booked else Slot
            rows.append({"session": session, "student": student, "teacher": teacher,
                         "booked": booked, "free": free})
        mine = Slot.search([("partner_id", "=", partner.id), ("session_id", "in", [o[0].id for o in offers])])
        values = self._prepare_portal_layout_values()
        values.update({"page_name": "school_meetings", "rows": rows, "mine": mine, "error": error})
        return request.render("bf_school_meeting.portal_school_meetings", values)

    @http.route("/my/school/meetings/book", type="http", auth="user", methods=["POST"], website=True)
    def portal_school_meeting_book(self, slot_id=None, student_id=None, **kw):
        slot = request.env["bf.school.meeting.slot"].sudo().browse(int(slot_id or 0)).exists()
        student = request.env["res.partner"].sudo().browse(int(student_id or 0)).exists()
        if not slot or not student:
            raise request.not_found()
        try:
            slot._school_book(student, request.env.user.partner_id)
        except UserError as error:
            request.session["school_meeting_error"] = str(error.args[0])
            return request.redirect("/my/school/meetings")
        return request.redirect("/my/school/meetings")

    @http.route("/my/school/meetings/cancel", type="http", auth="user", methods=["POST"], website=True)
    def portal_school_meeting_cancel(self, slot_id=None, **kw):
        slot = request.env["bf.school.meeting.slot"].sudo().browse(int(slot_id or 0)).exists()
        if not slot:
            raise request.not_found()
        try:
            slot._school_cancel(request.env.user.partner_id)
        except UserError as error:
            request.session["school_meeting_error"] = str(error.args[0])
            return request.redirect("/my/school/meetings")
        return request.redirect("/my/school/meetings")
