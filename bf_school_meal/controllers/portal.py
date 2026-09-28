from datetime import timedelta

from odoo import http
from odoo.exceptions import UserError, ValidationError
from odoo.http import request

from odoo.addons.portal.controllers.portal import CustomerPortal


class SchoolMealPortal(CustomerPortal):

    def _meal_context(self):
        partner = request.env.user.partner_id
        links = partner._school_portal_links().filtered(lambda l: l.is_payer or l.has_parental_authority)
        return partner, links.student_id

    @http.route("/my/school/meals", type="http", auth="user", website=True)
    def portal_school_meals(self, **kw):
        partner, children = self._meal_context()
        Order = request.env["bf.school.meal.order"].sudo()
        schools = children.student_group_ids.school_id
        today = schools[:1]._school_now().date() if schools else None
        days = request.env["bf.school.meal.day"].sudo().search(
            [("school_id", "in", schools.ids), ("date", ">=", today), ("date", "<=", today + timedelta(days=28))]
        ) if schools else request.env["bf.school.meal.day"]
        orders = Order.search([("student_id", "in", children.ids), ("day_id", "in", days.ids),
                               ("state", "=", "ordered")])
        accounts = request.env["bf.school.meal.account"].sudo().search([("partner_id", "=", partner.id)])
        values = self._prepare_portal_layout_values()
        values.update({
            "page_name": "school_meals", "children": children, "days": days, "orders": orders,
            "accounts": accounts, "schools": schools,
            "message": request.session.pop("school_meal_message", None),
        })
        return request.render("bf_school_meal.portal_school_meals", values)

    def _back(self, error=None):
        request.session["school_meal_message"] = error
        return request.redirect("/my/school/meals")

    @http.route("/my/school/meals/order", type="http", auth="user", methods=["POST"], website=True)
    def portal_school_meal_order(self, student_id=None, day_id=None, item_id=None, **kw):
        partner, children = self._meal_context()
        student = children.filtered(lambda c: str(c.id) == str(student_id))
        day = request.env["bf.school.meal.day"].sudo().browse(int(day_id or 0)).exists()
        item = request.env["bf.school.meal.item"].sudo().browse(int(item_id or 0)).exists()
        if not (student and day and item):
            raise request.not_found()
        try:
            request.env["bf.school.meal.order"]._school_place(partner, student, day, item)
        except (UserError, ValidationError) as error:
            return self._back(str(error.args[0]))
        return self._back()

    @http.route("/my/school/meals/<int:order_id>/cancel", type="http", auth="user", methods=["POST"], website=True)
    def portal_school_meal_cancel(self, order_id, **kw):
        partner, children = self._meal_context()
        order = request.env["bf.school.meal.order"].sudo().browse(order_id).exists()
        if not order or order.student_id not in children:
            raise request.not_found()
        try:
            order._school_cancel_by(partner)
        except UserError as error:
            return self._back(str(error.args[0]))
        return self._back()

    @http.route("/my/school/meals/topup", type="http", auth="user", methods=["POST"], website=True)
    def portal_school_meal_topup(self, account_id=None, amount=None, **kw):
        partner = request.env.user.partner_id
        account = request.env["bf.school.meal.account"].sudo().browse(int(account_id or 0)).exists()
        if not account or account.partner_id != partner:
            raise request.not_found()
        try:
            topup = account._school_topup(float(amount or 0))
        except (UserError, ValueError) as error:
            return self._back(str(error.args[0]))
        return request.redirect(topup.invoice_id.get_portal_url())

    @http.route("/my/school/meals/mode", type="http", auth="user", methods=["POST"], website=True)
    def portal_school_meal_mode(self, account_id=None, mode=None, **kw):
        partner, children = self._meal_context()
        account = request.env["bf.school.meal.account"].sudo().browse(int(account_id or 0)).exists()
        if not account or account.partner_id != partner or mode not in ("prepaid", "monthly"):
            raise request.not_found()
        school = children.student_group_ids.school_id.filtered(lambda s: s.company_id == account.company_id)[:1]
        if not school:
            raise request.not_found()
        try:
            account._school_set_mode(mode, school)
        except UserError as error:
            return self._back(str(error.args[0]))
        return self._back()
