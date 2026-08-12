# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
from odoo import http
from odoo.http import request
from odoo.addons.portal.controllers.portal import CustomerPortal
from odoo.addons.portal.controllers.portal import pager as portal_pager


class DonorPortal(CustomerPortal):
    def _prepare_home_portal_values(self, counters):
        values = super()._prepare_home_portal_values(counters)
        partner = request.env.user.partner_id.commercial_partner_id
        if "donation_count" in counters:
            values["donation_count"] = (
                request.env["donation.donation"]
                .sudo()
                .search_count(
                    [("partner_id", "child_of", partner.id), ("state", "=", "done")]
                )
            )
        if "tax_receipt_count" in counters:
            values["tax_receipt_count"] = (
                request.env["donation.tax.receipt"]
                .sudo()
                .search_count([("partner_id", "=", partner.id)])
            )
        return values

    @http.route(
        ["/my/donations", "/my/donations/page/<int:page>"],
        type="http",
        auth="user",
        website=True,
    )
    def portal_my_donations(self, page=1, **kw):
        partner = request.env.user.partner_id.commercial_partner_id
        Donation = request.env["donation.donation"].sudo()
        domain = [("partner_id", "child_of", partner.id), ("state", "=", "done")]
        total = Donation.search_count(domain)
        pager = portal_pager(
            url="/my/donations", total=total, page=page, step=20
        )
        donations = Donation.search(
            domain, limit=20, offset=pager["offset"], order="donation_date desc"
        )
        values = {
            "donations": donations,
            "pager": pager,
            "page_name": "donation",
            "default_url": "/my/donations",
        }
        return request.render("bf_fundraising_web.portal_my_donations", values)

    @http.route(
        ["/my/tax_receipts", "/my/tax_receipts/page/<int:page>"],
        type="http",
        auth="user",
        website=True,
    )
    def portal_my_tax_receipts(self, page=1, **kw):
        partner = request.env.user.partner_id.commercial_partner_id
        Receipt = request.env["donation.tax.receipt"].sudo()
        domain = [("partner_id", "=", partner.id)]
        total = Receipt.search_count(domain)
        pager = portal_pager(
            url="/my/tax_receipts", total=total, page=page, step=20
        )
        receipts = Receipt.search(
            domain, limit=20, offset=pager["offset"], order="date desc"
        )
        values = {
            "receipts": receipts,
            "pager": pager,
            "page_name": "tax_receipt",
            "default_url": "/my/tax_receipts",
        }
        return request.render("bf_fundraising_web.portal_my_tax_receipts", values)

    @http.route(
        ["/my/tax_receipt/<int:receipt_id>/download"],
        type="http",
        auth="user",
        website=True,
    )
    def portal_tax_receipt_download(self, receipt_id, **kw):
        partner = request.env.user.partner_id.commercial_partner_id
        receipt = request.env["donation.tax.receipt"].sudo().browse(receipt_id)
        if not receipt.exists() or receipt.partner_id.id != partner.id:
            return request.not_found()
        pdf, _content_type = (
            request.env["ir.actions.report"]
            .sudo()
            ._render_qweb_pdf(
                "donation_base.report_donation_tax_receipt", receipt.ids
            )
        )
        filename = "Recu-%s.pdf" % (receipt.number or receipt.id)
        headers = [
            ("Content-Type", "application/pdf"),
            ("Content-Length", len(pdf)),
            ("Content-Disposition", 'attachment; filename="%s"' % filename),
        ]
        return request.make_response(pdf, headers=headers)
