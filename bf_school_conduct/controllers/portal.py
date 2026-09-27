from odoo import http
from odoo.http import request

from odoo.addons.portal.controllers.portal import CustomerPortal


class SchoolConductPortal(CustomerPortal):

    @http.route("/my/school/conduct", type="http", auth="user", website=True)
    def portal_school_conduct(self, **kw):
        children = request.env.user.partner_id._school_portal_links().filtered("receives_notices").student_id
        # 🔴 A family sees a breach only once it has been informed of it: the school decides
        # when (and, for sexual violence from 14, only with the student's consent).
        incidents = request.env["bf.school.incident"].sudo().search([
            ("student_id", "in", children.ids), ("parents_informed_on", "!=", False)], limit=100)
        news = request.env["bf.school.followup.communication"].sudo().search([
            ("student_id", "in", children.ids), ("channel", "=", "email")], limit=100)
        values = self._prepare_portal_layout_values()
        values.update({"page_name": "school_conduct", "incidents": incidents, "news": news})
        return request.render("bf_school_conduct.portal_school_conduct", values)
