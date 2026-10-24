from odoo import _, http
from odoo.tools import format_amount
from odoo.http import request

from odoo.addons.bf_school_childcare.controllers.portal import ERRORS, SchoolChildcarePortal


class SchoolChildcareBillingPortal(SchoolChildcarePortal):
    """The books of tickets on the family's child-care page.

    🔴 As the rest of the page: the adults with parental authority only, every id checked against
    their children before anything is read in sudo.
    """

    def _childcare_card(self, child):
        card = super()._childcare_card(child)
        school = card.get("school")
        if not school:
            return card
        today = card["today"]
        Year = request.env["bf.school.year"].sudo()
        year = Year.search([("school_id", "=", school.id), ("date_start", "<=", today), ("date_end", ">=", today)],
                           limit=1)
        rate = request.env["bf.school.childcare.rate"]._school_rate(school, year, "ticket_book") if year else None
        books = request.env["bf.school.childcare.ticket_book"].sudo().search([
            ("student_id", "=", child.id), ("state", "in", ("to_pay", "paid"))] + (
            [("year_id", "=", year.id)] if year else [("id", "=", 0)]))
        partner = request.env.user.partner_id
        card.update({
            "ticket_rate": rate,
            "ticket_buy_label": rate and _("Buy %(count)s tickets, %(price)s", count=rate.tickets,
                                           price=format_amount(request.env, rate.amount, rate.currency_id)),
            # The adult's own books only: in shared custody, what the other parent bought is theirs.
            "tickets_left": sum(books.filtered(lambda b: b.state == "paid" and b.payer_id == partner).mapped("remaining")),
            # Only the invoices of the adult reading the page: the other parent's are theirs.
            "books_to_pay": books.filtered(lambda b: b.state == "to_pay" and b.payer_id == partner),
            "book_labels": {book.id: _("Pay the book of %s tickets", book.tickets) for book in books},
        })
        return card

    @http.route("/my/school/childcare/tickets/buy", type="http", auth="user", methods=["POST"], website=True)
    def portal_school_childcare_tickets_buy(self, student_id=None, **kw):
        partner, children = self._childcare_children()
        student = self._child(children, student_id)
        try:
            book = request.env["bf.school.childcare.ticket_book"]._school_buy(partner, student)
        except ERRORS as error:
            return self._back(str(error.args[0]), error=True)
        return request.redirect(book.sudo().invoice_id.get_portal_url())

    @http.route("/my/school/childcare/tickets/<int:book_id>/pay", type="http", auth="user", website=True)
    def portal_school_childcare_tickets_pay(self, book_id, **kw):
        partner, _children = self._childcare_children()
        book = request.env["bf.school.childcare.ticket_book"].sudo().browse(book_id).exists()
        if not book or book.payer_id != partner or book.state != "to_pay":
            raise request.not_found()
        return request.redirect(book.invoice_id.get_portal_url())
