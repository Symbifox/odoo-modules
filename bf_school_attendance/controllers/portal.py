from datetime import timedelta

from odoo import _, fields, http
from odoo.exceptions import ValidationError
from odoo.http import request

from odoo.addons.portal.controllers.portal import CustomerPortal

#: How far back a family may justify, and how long a declared absence may run.
JUSTIFY_DAYS_BACK = 30
MAX_SPAN_DAYS = 30


class SchoolAttendancePortal(CustomerPortal):

    def _children(self):
        return request.env["bf.school.attendance.line"]._school_portal_children(request.env.user.partner_id)

    def _declarable(self):
        # 🔴 Declaring an absence commits the child: parental authority, not notices alone.
        return request.env.user.partner_id._school_authority_links().student_id

    @http.route("/my/school/attendance", type="http", auth="user", website=True)
    def portal_school_attendance(self, **kw):
        children = self._children()
        since = fields.Date.context_today(request.env.user) - timedelta(days=JUSTIFY_DAYS_BACK)
        lines = request.env["bf.school.attendance.line"].sudo().search([
            ("student_id", "in", children.ids), ("status", "in", ("absent", "late")),
            ("date", ">=", since), ("session_id.state", "=", "done")], order="date desc, id desc")
        reasons = request.env["bf.school.absence.reason"].sudo().search([("portal_selectable", "=", True)])
        values = self._prepare_portal_layout_values()
        values.update({
            "page_name": "school_attendance", "children": children, "lines": lines,
            "declarable": self._declarable(),
            "reasons": reasons, "today": fields.Date.context_today(request.env.user),
            "message": request.session.pop("school_attendance_message", None),
        })
        return request.render("bf_school_attendance.portal_school_attendance", values)

    @http.route("/my/school/attendance/declare", type="http", auth="user", methods=["POST"], website=True)
    def portal_school_attendance_declare(self, student_id=None, date_from=None, date_to=None,
                                         part="full", reason_id=None, comment=None, **kw):
        student = self._declarable().filtered(lambda s: str(s.id) == str(student_id))
        if not student:
            raise request.not_found()
        reason = request.env["bf.school.absence.reason"].sudo().browse(int(reason_id or 0)).exists()
        message = None
        try:
            start = fields.Date.to_date(date_from)
            end = fields.Date.to_date(date_to or date_from)
        except ValueError:
            start = end = None
        today = fields.Date.context_today(request.env.user)
        if not reason or not reason.portal_selectable:
            message = _("Choose a reason.")
        elif not start or not end:
            message = _("The dates are not valid.")
        elif start < today - timedelta(days=JUSTIFY_DAYS_BACK):
            message = _("For an absence older than %s days, contact the school office.", JUSTIFY_DAYS_BACK)
        elif (end - start).days > MAX_SPAN_DAYS:
            message = _("For an absence longer than %s days, contact the school office.", MAX_SPAN_DAYS)
        if not message:
            try:
                request.env["bf.school.absence.declaration"].sudo().create({
                    "student_id": student.id, "date_from": start, "date_to": end,
                    "part": part if part in ("full", "am", "pm") else "full",
                    "reason_id": reason.id, "comment": (comment or "").strip()[:1000],
                    "declared_by_id": request.env.user.partner_id.id, "source": "portal"})
                message = _("Thank you: the absence is recorded and the school is informed.")
            except ValidationError as error:
                message = str(error.args[0])
        request.session["school_attendance_message"] = message
        return request.redirect("/my/school/attendance")
