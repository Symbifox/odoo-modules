# -*- coding: utf-8 -*-
"""Les icônes des sites, servies par adresse pour que le navigateur les garde.

Avant, chaque ouverture du menu Signets retéléchargeait toutes les icônes en base64
dans la réponse de `widget_data`. L'adresse porte un jeton signé lié à l'image
(`bf.bookmark.favicon._token`) : seul `widget_data`, qui ne la donne que pour des
signets lus avec les droits de la personne, sait la fabriquer. Une adresse change
quand l'image change, donc la réponse peut se garder sans limite.
"""
import base64

from odoo import http
from odoo.http import request
from odoo.tools import consteq

from ..models.bookmark_favicon import ICON_ROUTE

CACHE_CONTROL = "private, max-age=31536000, immutable"


class BookmarkIcons(http.Controller):

    @http.route(f"{ICON_ROUTE}/<int:favicon_id>/<string:token>.png", type="http",
                auth="user", methods=["GET"], readonly=True)
    def bookmark_icon(self, favicon_id, token, **_kw):
        favicon = request.env["bf.bookmark.favicon"].sudo().browse(favicon_id).exists()
        # « Inconnue », « pas d'image » et « mauvais jeton » rendent la MÊME réponse.
        if not favicon or favicon.state != "ok" or not favicon.image \
                or not consteq(token, favicon._token()):
            raise request.not_found()
        png = base64.b64decode(favicon.image)
        return request.make_response(png, headers=[
            ("Content-Type", "image/png"),
            ("Content-Length", str(len(png))),
            ("Cache-Control", CACHE_CONTROL),
            ("X-Content-Type-Options", "nosniff"),
        ])
