from odoo import http
from odoo.http import request

from odoo.addons.portal.controllers.portal import CustomerPortal


class SchoolPortal(CustomerPortal):

    def _prepare_home_portal_values(self, counters):
        values = super()._prepare_home_portal_values(counters)
        if "school_child_count" in counters:
            values["school_child_count"] = len(
                request.env.user.partner_id._school_portal_links().student_id)
        return values

    @staticmethod
    def _school_child_cards(links):
        """One card per child, with the groups of the current year and this adult's roles."""
        cards = []
        for student, student_links in links.grouped("student_id").items():
            link = student_links[:1]
            groups = student.student_enrollment_ids.filtered(
                lambda e: e.state == "active" and e.year_id.state == "current").group_id
            cards.append({
                "student": student,
                "groups": groups,
                "roles": {
                    "receives_notices": link.receives_notices,
                    "can_sign": link.can_sign,
                    "is_payer": link.is_payer,
                    "can_pickup": link.can_pickup,
                },
            })
        return sorted(cards, key=lambda c: c["student"].name or "")

    @http.route("/my/school", type="http", auth="user", website=True)
    def portal_my_school(self, **kw):
        links = request.env.user.partner_id._school_portal_links()
        values = self._prepare_portal_layout_values()
        values.update({
            "page_name": "school",
            "cards": self._school_child_cards(links),
        })
        return request.render("bf_school_portal.portal_my_school", values)
