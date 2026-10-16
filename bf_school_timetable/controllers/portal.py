from datetime import timedelta

from odoo import fields, http
from odoo.http import request

from odoo.addons.portal.controllers.portal import CustomerPortal


class SchoolTimetablePortal(CustomerPortal):

    def _children(self):
        """The minor children whose notices this adult receives: of age, a student's timetable is theirs."""
        Feed = request.env["bf.school.timetable.feed"]
        links = request.env.user.partner_id._school_portal_links().filtered("receives_notices")
        return links.student_id.filtered(lambda student: Feed._is_minor(student))

    @http.route("/my/school/timetable", type="http", auth="user", website=True)
    def portal_school_timetable(self, **kw):
        children = self._children()
        Feed = request.env["bf.school.timetable.feed"].sudo()
        today = fields.Date.context_today(request.env.user)
        guardian = request.env.user.partner_id
        week = []
        for student in children:
            days = {}
            for enrollment in student.sudo().student_enrollment_ids.filtered(lambda e: e.state == "active"):
                # Each date of the week comes from the timetable in force on it.
                for timetable, dates in request.env["bf.school.timetable"].sudo()._published_by_date(
                        enrollment.school_id, today, today + timedelta(days=6)):
                    slots = timetable.slot_ids.filtered(lambda s, g=enrollment.group_id: s.group_id == g)
                    for item in timetable._occurrences(min(dates), max(dates), slots):
                        if item["date"] in dates:
                            days.setdefault(item["date"], []).append(item)
            week.append({"student": student, "days": sorted(days.items()), "feed": Feed.search(
                [("student_id", "=", student.id), ("guardian_id", "=", guardian.id)], limit=1)})
        values = self._prepare_portal_layout_values()
        values.update({"page_name": "school_timetable", "children": children, "week": week,
                       "base_url": request.httprequest.host_url.rstrip("/")})
        return request.render("bf_school_timetable.portal_school_timetable", values)

    @http.route("/my/school/timetable/link", type="http", auth="user", methods=["POST"], website=True)
    def portal_school_timetable_link(self, student_id=None, **kw):
        student = self._children().filtered(lambda s: str(s.id) == str(student_id))
        if not student:
            raise request.not_found()
        request.env["bf.school.timetable.feed"]._rotate_for(student, request.env.user.partner_id)
        return request.redirect("/my/school/timetable")

    # The address is the secret: no session cookie is set on it (it is not a visit), and a page that links to it
    # does not learn it from the Referer.
    @http.route("/school/timetable/<string:token>.ics", type="http", auth="public", csrf=False, save_session=False)
    def feed(self, token, **kw):
        feed = request.env["bf.school.timetable.feed"].sudo().search([("token", "=", token)], limit=1)
        # 🔴 A link that stopped being the adult's to hold answers as an unknown one: nothing says it ever existed.
        if not feed or not feed._may_be_read():
            raise request.not_found()
        if not feed.last_read_on or fields.Datetime.now() - feed.last_read_on > timedelta(hours=1):
            feed.last_read_on = fields.Datetime.now()
        return request.make_response(feed._ics(), headers=[
            ("Content-Type", "text/calendar; charset=utf-8"),
            ("Content-Disposition", 'inline; filename="timetable.ics"'),
            ("Cache-Control", "private, max-age=900"), ("X-Robots-Tag", "noindex, nofollow"),
            ("Referrer-Policy", "no-referrer"), ("X-Content-Type-Options", "nosniff")])
