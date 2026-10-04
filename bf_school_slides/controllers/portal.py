from odoo import http
from odoo.http import request

from odoo.addons.portal.controllers.portal import CustomerPortal
from odoo.addons.website_profile.controllers.main import WebsiteProfile


class SchoolSlidesPortal(CustomerPortal):

    @http.route("/my/school/courses", type="http", auth="user", website=True)
    def portal_school_courses(self, **kw):
        """The school courses this person attends: a student's own, a parent's through the child."""
        memberships = request.env["slide.channel.partner"].sudo().search([
            ("partner_id", "=", request.env.user.partner_id.id),
            ("member_status", "!=", "invited"),
            ("channel_id.school_group_ids", "!=", False),
            ("channel_id.website_published", "=", True),
        ])
        values = self._prepare_portal_layout_values()
        values.update({"page_name": "school_courses",
                       "courses": memberships.channel_id.sorted("name")})
        return request.render("bf_school_slides.portal_school_courses", values)


class SchoolWebsiteProfile(WebsiteProfile):

    @http.route()
    def send_validation_email(self, **kwargs):
        # The address on a student's card is often a parent's; validating it only fed karma.
        if request.env.user._school_is_student_user():
            return False
        return super().send_validation_email(**kwargs)
