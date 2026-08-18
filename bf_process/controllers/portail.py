# -*- coding: utf-8 -*-
"""Ce que le téléphone ouvre après le scan.

Deux routes, et pas une de plus : la page d'une étape, et le fichier d'une de
ses ressources. Tout passe par le jeton de l'étape, vérifié à chaque appel —
y compris sur le fichier, sinon il suffirait de deviner un identifiant de
pièce jointe pour se passer du jeton.
"""
from odoo import http
from odoo.exceptions import AccessError, MissingError
from odoo.http import content_disposition, request

from odoo.addons.portal.controllers.portal import CustomerPortal

GENRES = {
    "procedure": "Procédure",
    "fiche": "Fiche signalétique",
    "formation": "Formation",
    "video": "Vidéo",
    "gabarit": "Gabarit",
    "reference": "Référence",
}


class CarteAtelier(CustomerPortal):

    def _etape(self, node_id, access_token):
        """L'étape, si le jeton le permet. Sinon, une erreur d'accès."""
        return self._document_check_access(
            "bf.process.node", node_id, access_token)

    def _ctx(self, noeud, jeton):
        """Ce que le gabarit reçoit : jamais l'enregistrement lui-même."""
        ressources = noeud.resource_ids.sorted(
            key=lambda r: (not r.critique, r.sequence, r.id))
        return {
            "etape": noeud.name or noeud.code,
            "processus": "%s · v%s" % (noeud.process_id.name,
                                       noeud.process_id.version),
            "couloir": noeud.lane_id.name or "",
            "niveau": noeud.diagram_id.title or "",
            "node_id": noeud.id,
            "jeton": jeton or "",
            "ressources": [{
                "id": r.id,
                "nom": r.name,
                "genre": GENRES.get(r.kind, r.kind),
                "critique": r.critique,
                "note": r.note or "",
                "externe": bool(r.url),
                "url": r.url or "",
            } for r in ressources],
        }

    @http.route(["/carte/etape/<int:node_id>"], type="http", auth="public",
                website=True, sitemap=False)
    def page_etape(self, node_id, access_token=None, **kw):
        try:
            noeud = self._etape(node_id, access_token)
        except (AccessError, MissingError):
            return request.render("bf_process.portail_etape_refus", {})
        return request.render("bf_process.portail_etape",
                              self._ctx(noeud, access_token))

    @http.route(["/carte/etape/<int:node_id>/ressource/<int:res_id>"],
                type="http", auth="public", sitemap=False)
    def fichier_ressource(self, node_id, res_id, access_token=None, **kw):
        """Le fichier d'une ressource, servi sous le jeton de SON étape.

        `res_id` est vérifié comme appartenant à `node_id` : sans ça, un jeton
        valide sur une étape servirait n'importe quelle pièce jointe de la
        base, ce qui est exactement le trou qu'on croit avoir fermé.
        """
        try:
            noeud = self._etape(node_id, access_token)
        except (AccessError, MissingError):
            return request.not_found()
        ressource = noeud.resource_ids.filtered(lambda r: r.id == res_id)
        if not ressource:
            return request.not_found()
        ressource = ressource[0]
        if ressource.url:
            # ⚠️ `local=False` est obligatoire : `request.redirect` est LOCAL
            # par défaut et réécrit « https://fournisseur/sds.pdf » en
            # « /sds.pdf », que le serveur Odoo sert lui-même en 404. Une fiche
            # signalétique hébergée chez le fournisseur — le cas normal — ne
            # s'ouvrait donc jamais.
            return request.redirect(ressource.url, local=False)
        piece = ressource.sudo().attachment_id
        if not piece:
            # une politique versionnée : on sert le fichier de sa version
            # courante, jamais le corps interne, qui n'est pas public
            piece = ressource.sudo().document_id.current_version_id.attachment_id
        if not piece:
            return request.not_found()
        donnees = piece.raw
        return request.make_response(donnees, headers=[
            ("Content-Type", piece.mimetype or "application/octet-stream"),
            ("Content-Length", len(donnees)),
            ("Content-Disposition", content_disposition(piece.name)),
        ])
