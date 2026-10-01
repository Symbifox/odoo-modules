from odoo import http
from odoo.http import request
from odoo.tools import consteq

from odoo.addons.portal.controllers.portal import CustomerPortal


class HelpdeskClientNotifyController(http.Controller):
    """Couper ou rétablir les courriels d'un billet, depuis le lien du courriel.

    Le GET affiche une page de confirmation ; seul le POST change quelque
    chose. Les filtres de liens des messageries ouvrent chaque lien d'un
    courriel : un GET qui couperait les courriels le ferait à l'insu du client.
    """

    def _ticket(self, ticket_id, partner_id, token):
        ticket = request.env["helpdesk.ticket"].sudo().browse(ticket_id).exists()
        partner = request.env["res.partner"].sudo().browse(partner_id).exists()
        if not ticket or not partner or not token:
            return None, None
        if not consteq(ticket._bf_mute_token(partner.id), token):
            return None, None
        return ticket, partner

    @http.route("/helpdesk/courriels/<int:ticket_id>/<int:partner_id>/<string:token>",
                type="http", auth="public", website=True, methods=["GET", "POST"],
                sitemap=False)
    def ticket_emails(self, ticket_id, partner_id, token, **kw):
        ticket, partner = self._ticket(ticket_id, partner_id, token)
        if not ticket:
            return request.render("bf_helpdesk.csat_closed", {"csat": False})
        done = False
        if request.httprequest.method == "POST":
            muted = kw.get("action") == "mute"
            ticket._bf_set_muted(partner, muted)
            done = True
        return request.render("bf_helpdesk.ticket_emails_page", {
            "ticket": ticket,
            "partner": partner,
            "muted": partner in ticket.client_muted_partner_ids,
            "done": done,
            "action_url": request.httprequest.path,
        })


class HelpdeskPortalNotify(CustomerPortal):

    @http.route("/my/ticket/<int:ticket_id>/courriels", type="http", auth="user",
                website=True, methods=["POST"])
    def portal_ticket_emails(self, ticket_id, access_token=None, action=None, **kw):
        ticket_sudo = self._document_check_access("helpdesk.ticket", ticket_id, access_token)
        partner = request.env.user.partner_id
        ticket_sudo._bf_set_muted(partner, action == "mute")
        return request.redirect("/my/ticket/%s" % ticket_id)

    @http.route("/my/helpdesk/preferences", type="http", auth="user",
                website=True, methods=["POST"])
    def portal_helpdesk_preferences(self, mode=None, **kw):
        if mode in ("each", "daily"):
            request.env.user.partner_id.sudo().helpdesk_notify_mode = mode
        return request.redirect("/my/tickets")
