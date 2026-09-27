# -*- coding: utf-8 -*-
"""Le lien « lire » : marque l'élément lu pour la personne, puis mène à l'article.

C'est le lien que portent les cartes Discuss et le résumé courriel : lire depuis
n'importe où retire l'élément de « À lire ». La redirection ne va jamais
ailleurs qu'au lien enregistré de l'élément, et seulement en http(s) : ce
n'est pas une redirection ouverte.
"""
import re

from werkzeug.exceptions import NotFound

from odoo import http
from odoo.http import request


class FluxLecture(http.Controller):

    @http.route("/flux/lire/<int:element_id>", type="http", auth="user", methods=["GET"])
    def lire(self, element_id, **kw):
        element = request.env["bf.flux.element"].browse(element_id).exists()
        if not element or not re.match(r"^https?://", element.lien or "", re.I):
            raise NotFound()
        element.check_access("read")
        request.env["bf.flux.lecture"]._flux_marquer(element, request.env.user)
        return request.redirect(element.lien, code=303, local=False)
