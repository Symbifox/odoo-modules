# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
from odoo import http
from odoo.http import request


class FundraisingWebController(http.Controller):
    def _form_render_values(self, post=None, error=None):
        return {
            "funds": request.env["bf.fund"].sudo().search([("active", "=", True)]),
            "campaigns": request.env["donation.campaign"]
            .sudo()
            .search([("active", "=", True)]),
            "values": post or {},
            "error": error or {},
        }

    @http.route(["/don"], type="http", auth="public", website=True, sitemap=True)
    def donation_form(self, **kw):
        return request.render(
            "bf_fundraising_web.donation_form", self._form_render_values(post=kw)
        )

    @http.route(
        ["/don/submit"],
        type="http",
        auth="public",
        website=True,
        methods=["POST"],
    )
    def donation_submit(self, **post):
        error = {}
        name = (post.get("name") or "").strip()
        email = (post.get("email") or "").strip()
        try:
            amount = float((post.get("amount") or "0").replace(",", "."))
        except ValueError:
            amount = 0.0
        if not name:
            error["name"] = True
        if not email or "@" not in email:
            error["email"] = True
        if amount <= 0:
            error["amount"] = True
        if error:
            return request.render(
                "bf_fundraising_web.donation_form",
                self._form_render_values(post=post, error=error),
            )

        Partner = request.env["res.partner"].sudo()
        partner = Partner.search([("email", "=ilike", email)], limit=1)
        if not partner:
            partner = Partner.create(
                {
                    "name": name,
                    "email": email,
                    "is_constituent": True,
                    "company_type": "person",
                    "street": post.get("street") or False,
                    "city": post.get("city") or False,
                    "zip": post.get("zip") or False,
                }
            )

        def _valid_id(model, val):
            """Return the id only if it refers to an existing record, so a
            tampered/stale fund/campaign id is silently ignored rather than
            raising a foreign-key error."""
            try:
                rid = int(val) if val else 0
            except (TypeError, ValueError):
                return False
            if rid and request.env[model].sudo().browse(rid).exists():
                return rid
            return False

        fund_id = _valid_id("bf.fund", post.get("fund_id"))
        campaign_id = _valid_id("donation.campaign", post.get("campaign_id"))
        donation = (
            request.env["donation.donation"]
            .sudo()
            .create_web_donation(partner.id, amount, fund_id, campaign_id)
        )
        return request.render(
            "bf_fundraising_web.donation_thanks",
            {"donation": donation, "partner": partner},
        )
