"""Public unsubscribe endpoint for CX solicitations.

GET shows a confirmation page (crawler-safe: email-provider link scanners
follow GETs - same lesson as the core rating module, which is why nothing
is recorded before the POST). POST adds the contact's email to
mail.blacklist, which every bf_cx outbound guard already enforces.

Token = keyed HMAC over the partner id (odoo.tools.hmac with the database
secret): no enumeration, no PII in the URL, constant-time comparison.
"""
import logging

from markupsafe import Markup, escape

from odoo import _, http
from odoo.http import request
from odoo.tools import consteq, hmac

_logger = logging.getLogger(__name__)


class BfCxUnsubscribe(http.Controller):

    @staticmethod
    def _check(partner_id, token):
        partner = request.env["res.partner"].sudo().browse(partner_id).exists()
        if not partner:
            return None
        expected = hmac(
            request.env(su=True), "bf_cx_unsubscribe", str(partner.id)
        )
        if not consteq(expected, token or ""):
            return None
        return partner

    def _page(self, inner_html):
        page = (
            "<!DOCTYPE html><html><head>"
            '<meta charset="utf-8"/>'
            '<meta name="viewport" content="width=device-width, initial-scale=1"/>'
            "<title>Blue Fox</title></head>"
            '<body style="margin:0; background-color:#F8FAFC; '
            "font-family:'Lexend','Segoe UI',Arial,sans-serif; color:#2D3031;\">"
            '<div style="max-width:560px; margin:48px auto; background:#fff; '
            'border:1px solid #e5e7eb; border-radius:12px; padding:32px;">'
            f"{inner_html}"
            "</div></body></html>"
        )
        return request.make_response(
            page, headers=[("Content-Type", "text/html; charset=utf-8")]
        )

    @http.route(
        "/cx/unsubscribe/<int:partner_id>/<string:token>",
        type="http",
        auth="public",
        methods=["GET"],
        sitemap=False,
    )
    def unsubscribe_confirm(self, partner_id, token, **kwargs):
        partner = self._check(partner_id, token)
        if not partner:
            return request.not_found()
        inner = (
            "<h2 style='margin:0 0 16px 0;'>Ne plus recevoir de demandes "
            "d'avis / Stop feedback requests</h2>"
            "<p>Vous ne recevrez plus de sondages ni de demandes "
            "d'évaluation de notre part. Vos courriels de service "
            "(factures, dossiers en cours) ne sont pas touchés.</p>"
            "<p style='color:#6B7280;'>You will no longer receive surveys "
            "or rating requests from us. Service emails (invoices, ongoing "
            "files) are not affected.</p>"
            f"<form method='post' action='/cx/unsubscribe/{partner.id}/"
            f"{escape(token)}'>"
            "<input type='hidden' name='csrf_token' value='%s'/>"
            "<button type='submit' style='background:#29ABE1; color:#fff; "
            "border:0; padding:12px 28px; border-radius:6px; font-size:15px; "
            "cursor:pointer;'>Me désabonner / Unsubscribe</button>"
            "</form>"
        ) % request.csrf_token()
        return self._page(Markup(inner))

    @http.route(
        "/cx/unsubscribe/<int:partner_id>/<string:token>",
        type="http",
        auth="public",
        methods=["POST"],
        sitemap=False,
    )
    def unsubscribe_do(self, partner_id, token, **kwargs):
        partner = self._check(partner_id, token)
        if not partner:
            return request.not_found()
        email = partner.email_normalized
        if email:
            request.env["mail.blacklist"].sudo()._add(
                email,
                message=_(
                    "Désabonnement des demandes d'avis (lien de courriel "
                    "Expérience client)."
                ),
            )
            partner.sudo().message_post(
                body=_(
                    "S'est désabonné des demandes d'avis (sondages et "
                    "évaluations) via le lien de courriel."
                )
            )
        inner = (
            "<h2 style='margin:0 0 16px 0;'>C'est fait / Done</h2>"
            "<p>Vous ne recevrez plus de demandes d'avis de notre part. "
            "Merci, et n'hésitez pas à nous écrire directement si quelque "
            "chose ne va pas.</p>"
            "<p style='color:#6B7280;'>You will no longer receive feedback "
            "requests from us. Thank you, and do reach out directly if "
            "anything is wrong.</p>"
        )
        return self._page(Markup(inner))
