import base64
import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)

# 1x1 transparent PNG.
_PIXEL = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


class BfSecurityAwarenessController(http.Controller):
    """Public landing endpoints for simulated phishing.

    Every route is anonymous and returns CONSTANT behaviour for unknown tokens
    (the same generic teachable page, same status) so an attacker cannot probe
    which tokens are valid. Tokens are 256-bit and never echoed back.

    The credential page records only THAT a submission happened and, optionally,
    the LENGTH of each field. The raw username/password are never assigned to a
    field, never logged, and never written to the chatter.
    """

    def _result_by_token(self, token):
        if not token:
            return request.env["bf.phishing.result"]
        return request.env["bf.phishing.result"].sudo().search(
            [("token", "=", token)], limit=1)

    def _country_id(self):
        try:
            country_code = (request.geoip.country_code or "").upper()
        except Exception:  # noqa: BLE001
            country_code = ""
        if not country_code:
            return False
        country = request.env["res.country"].sudo().search(
            [("code", "=", country_code)], limit=1)
        return country.id or False

    def _render_lesson(self, result):
        """Teachable page — also the constant fallback for invalid tokens."""
        template = result.campaign_id.template_id if result else False
        return request.render(
            "bf_security_awareness.phishing_lesson",
            {
                "result": result,
                "template": template,
                "teachable_html": template.teachable_html if template else False,
                "training_url": (
                    "/slides/%s" % template.training_channel_id.id
                    if template and template.training_channel_id else False),
            },
        )

    # ------------------------------------------------------------------ #
    # Landing (click)
    # ------------------------------------------------------------------ #
    @http.route(["/phish/<string:token>"], type="http", auth="public",
                website=True, sitemap=False)
    def phish_landing(self, token, **kw):
        result = self._result_by_token(token)
        if not result:
            return self._render_lesson(request.env["bf.phishing.result"])

        result.register_click(
            ip=request.httprequest.remote_addr,
            country_id=self._country_id(),
            user_agent=request.httprequest.headers.get("User-Agent"),
        )

        mode = result.campaign_id.template_id.landing_mode
        if mode == "credential":
            return request.render(
                "bf_security_awareness.phishing_credential_form",
                {
                    "token": token,
                    "template": result.campaign_id.template_id,
                },
            )
        # awareness / link_only → straight to the teachable page.
        return self._render_lesson(result)

    # ------------------------------------------------------------------ #
    # Fake credential submission
    # ------------------------------------------------------------------ #
    @http.route(["/phish/<string:token>/submit"], type="http", auth="public",
                website=True, methods=["POST"], csrf=True)
    def phish_submit(self, token, **kw):
        result = self._result_by_token(token)
        if not result:
            return self._render_lesson(request.env["bf.phishing.result"])

        username_len = password_len = None
        if result.campaign_id.template_id.capture_field_lengths:
            # Read ONLY the length. The values are reduced to ints immediately
            # and never stored, logged, or otherwise retained.
            username_len = len(kw.get("login") or "")
            password_len = len(kw.get("password") or "")
        result.register_submit(
            username_len=username_len, password_len=password_len)

        return request.redirect("/phish/%s/lesson" % token)

    # ------------------------------------------------------------------ #
    # Teachable moment
    # ------------------------------------------------------------------ #
    @http.route(["/phish/<string:token>/lesson"], type="http", auth="public",
                website=True, sitemap=False)
    def phish_lesson(self, token, **kw):
        return self._render_lesson(self._result_by_token(token))

    # ------------------------------------------------------------------ #
    # Open tracking pixel
    # ------------------------------------------------------------------ #
    @http.route(["/phish/<string:token>/open.png"], type="http", auth="public",
                sitemap=False)
    def phish_open(self, token, **kw):
        result = self._result_by_token(token)
        if result:
            try:
                result.register_open()
            except Exception:  # noqa: BLE001 - tracking must never error out
                _logger.exception("bf_security_awareness: open tracking failed")
        return request.make_response(
            _PIXEL,
            headers=[
                ("Content-Type", "image/png"),
                ("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0"),
                ("Pragma", "no-cache"),
            ],
        )

    # ------------------------------------------------------------------ #
    # "I reported this" positive path
    # ------------------------------------------------------------------ #
    @http.route(["/phish/<string:token>/report"], type="http", auth="public",
                website=True, sitemap=False)
    def phish_report(self, token, **kw):
        result = self._result_by_token(token)
        if result:
            result.register_report()
        return request.render(
            "bf_security_awareness.phishing_reported",
            {"result": result},
        )
