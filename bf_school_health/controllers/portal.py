from datetime import timedelta

from odoo import fields, http
from odoo.exceptions import UserError
from odoo.http import request

from odoo.addons.portal.controllers.portal import CustomerPortal


class SchoolHealthPortal(CustomerPortal):

    @http.route("/my/school/health", type="http", auth="user", website=True)
    def portal_school_health(self, **kw):
        partner = request.env.user.partner_id
        links = partner._school_portal_links()
        children = links.filtered("receives_notices").student_id | links.filtered("can_sign").student_id
        Med = request.env["bf.school.medication"].sudo()
        since = fields.Datetime.now() - timedelta(days=60)
        values = self._prepare_portal_layout_values()
        values.update({
            "page_name": "school_health", "children": children.sudo(),
            "to_sign": Med.search([("student_id", "in", links.filtered("can_sign").student_id.ids),
                                   ("state", "=", "asked")]),
            "active": Med.search([("student_id", "in", children.ids), ("state", "=", "active")]),
            "given": request.env["bf.school.medication.administration"].sudo().search(
                [("student_id", "in", children.ids), ("given_on", ">=", since)], limit=100),
            "message": request.session.pop("school_health_message", None),
        })
        return request.render("bf_school_health.portal_school_health", values)

    def _answer(self, med, partner, decision):
        try:
            med._school_sign(partner, decision == "accept", ip=request.httprequest.remote_addr)
            return None
        except UserError as error:
            return str(error.args[0])

    @http.route("/my/school/health/<int:med_id>/answer", type="http", auth="user", methods=["POST"], website=True)
    def portal_school_health_answer(self, med_id, decision=None, **kw):
        med = request.env["bf.school.medication"].sudo().browse(med_id).exists()
        partner = request.env.user.partner_id
        # Another family's authorisation does not exist for this adult.
        if not med or partner not in med.student_id.student_guardian_link_ids.filtered("can_sign").guardian_id:
            raise request.not_found()
        error = self._answer(med, partner, decision)
        request.session["school_health_message"] = error
        return request.redirect("/my/school/health")
