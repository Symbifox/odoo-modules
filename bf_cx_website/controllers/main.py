"""Public testimonials page.

Testimonials are read from bf.cx.testimonial at request time, with a
strict domain on state='published'. Because the page is rendered
dynamically, a testimonial whose state leaves 'published' (retired,
declined, back to draft) disappears from the site instantly, which is
what the Law 25 consent-withdrawal flow of bf_cx expects.

Only plain display values (quote, client name, client company) are
handed to the template; the sudo recordset itself never reaches QWeb.
"""
import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class BfCxWebsiteController(http.Controller):

    def _bf_cx_testimonial_card(self, testimonial):
        """Return the display values for one published testimonial.

        The client company is the parent partner when the contact is a
        person, falling back to the commercial company name; when the
        partner is itself a company, its name is already the company so
        no separate line is shown.
        """
        partner = testimonial.partner_id
        author = partner.name or ""
        company = ""
        if not partner.is_company:
            company = (
                (partner.parent_id and partner.parent_id.name)
                or partner.commercial_company_name
                or ""
            )
        if company == author:
            company = ""
        return {
            "quote": testimonial.body or "",
            "author": author,
            "company": company,
        }

    @http.route(
        ["/temoignages"],
        type="http",
        auth="public",
        website=True,
        sitemap=True,
    )
    def bf_cx_temoignages(self, **kw):
        """Render the public testimonials page.

        Everything is wrapped so that a bad record or an unexpected
        error degrades to an empty page instead of a traceback: the
        public site must never break because of one testimonial.
        """
        cards = []
        try:
            domain = [("state", "=", "published")]
            website = getattr(request, "website", None)
            if website and website.company_id:
                domain.append(("company_id", "=", website.company_id.id))
            testimonials = request.env["bf.cx.testimonial"].sudo().search(domain)
            for testimonial in testimonials:
                try:
                    card = self._bf_cx_testimonial_card(testimonial)
                    if card["quote"] and card["author"]:
                        cards.append(card)
                except Exception:  # noqa: BLE001 - skip the record, keep the page
                    _logger.exception(
                        "bf_cx_website: testimonial %s skipped on /temoignages",
                        testimonial.id,
                    )
        except Exception:  # noqa: BLE001 - never break the public page
            _logger.exception("bf_cx_website: could not load testimonials")
            cards = []
        return request.render("bf_cx_website.temoignages_page", {"cards": cards})
