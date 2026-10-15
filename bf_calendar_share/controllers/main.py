# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
"""Les trois adresses d'un lien de partage (lot 5).

Publiques par nature : le jeton est la seule clé, et aucune adresse ne porte
un identifiant d'événement. Un jeton inconnu, révoqué, expiré ou d'un compte
désactivé reçoit la même réponse (404), pour ne rien apprendre à qui essaie.
"""

from werkzeug.exceptions import NotFound

from odoo import http
from odoo.http import request

ENTETES = [
    ("Cache-Control", "private, max-age=300"),
    ("X-Robots-Tag", "noindex, nofollow"),
    ("Referrer-Policy", "no-referrer"),
]


class BfCalendarShareController(http.Controller):

    def _lien(self, token, forme):
        link = request.env["bf.calendar.share.link"].sudo()._bf_lien_valide(token)
        if not link:
            raise NotFound()
        link._bf_journaliser(forme, request.httprequest.remote_addr,
                             request.httprequest.headers.get("User-Agent"))
        return link

    @http.route("/calendar/share/<string:token>.ics", type="http", auth="public",
                methods=["GET", "HEAD"], csrf=False, save_session=False)
    def bf_share_ics(self, token, **kw):
        link = self._lien(token, "ics")
        return request.make_response(link._bf_ics(), headers=ENTETES + [
            ("Content-Type", "text/calendar; charset=utf-8"),
            ("Content-Disposition", 'inline; filename="calendar.ics"'),
        ])

    @http.route("/calendar/share/<string:token>/freebusy.ifb", type="http", auth="public",
                methods=["GET", "HEAD"], csrf=False, save_session=False)
    def bf_share_freebusy(self, token, **kw):
        link = self._lien(token, "freebusy")
        return request.make_response(link._bf_freebusy(), headers=ENTETES + [
            ("Content-Type", "text/calendar; charset=utf-8"),
            ("Content-Disposition", 'inline; filename="freebusy.ifb"'),
        ])

    @http.route("/calendar/share/<string:token>", type="http", auth="public",
                methods=["GET", "HEAD"], csrf=False, save_session=False)
    def bf_share_page(self, token, **kw):
        link = self._lien(token, "page")
        link = link.with_context(lang=link.owner_id.lang)
        corps = request.env["ir.qweb"].sudo().with_context(lang=link.owner_id.lang)._render(
            "bf_calendar_share.share_page", {
                "link": link, "jours": link._bf_jours_pour_la_page(),
                "owner": link.owner_id.sudo(),
            })
        # Le doctype se pose ici : écrit dans le gabarit par t-out, il sortait
        # échappé, en texte visible en haut de la page (vu aux captures du 2026-10-15).
        return request.make_response("<!DOCTYPE html>\n" + str(corps), headers=ENTETES + [
            ("Content-Type", "text/html; charset=utf-8"),
        ])
