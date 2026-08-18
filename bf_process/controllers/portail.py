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

    def _corps_en_pdf(self, document):
        """La procédure rendue, quand sa version publiée n'a pas de fichier.

        Dans une base de connaissances tenue dans Odoo, le contenu d'une
        procédure vit dans son corps documentaire, pas en pièce jointe : sur
        les 191 versions publiées de la nôtre, deux portaient un fichier.
        S'en tenir au fichier revenait donc à n'ouvrir à peu près rien, et la
        cible « politique ou procédure » d'une ressource ne servait qu'à
        l'écran, jamais au mur.

        Le tri est fait en amont par `_corps_a_rendre` : ce qui arrive ici a
        déjà une version publiée et un corps qui vit dans Odoo.
        """
        # ⚠️ `env(su=True)` dès la RÉSOLUTION du rapport : l'utilisateur public
        # n'a pas le droit de lire `ir.actions.report`, et un `env.ref` nu
        # rendait 403 avant même d'arriver au rendu. Le rendu QWeb, lui, se
        # fait dans l'environnement du rapport — celui-là doit donc aussi être
        # élevé, sinon ni le document ni ses sections ne se lisent. Le jeton de
        # l'étape a déjà tranché l'accès, c'est lui la frontière.
        rapport = request.env(su=True).ref(
            "project_knowledge_matrix.action_report_document_body",
            raise_if_not_found=False)
        if not rapport:
            return request.not_found()
        # ⚠️ Sous `--test-enable`, Odoo rend le HTML plutôt que d'appeler
        # wkhtmltopdf, et rend « html » comme second terme. On lit donc le
        # genre PRODUIT au lieu de l'affirmer : autrement l'en-tête annoncerait
        # un PDF et livrerait du HTML. (Forcer le vrai rendu dans les tests ne
        # marche pas : wkhtmltopdf rappelle le serveur d'essai pour aller
        # chercher la feuille de style, et le curseur de la transaction est
        # déjà pris — quatre tests en erreur, 90 secondes.)
        rendu, genre = rapport._render_qweb_pdf(rapport.report_name,
                                                document.ids)
        nom = "%s-v%s.%s" % (document.code or document.name,
                             document.current_version or "0", genre)
        return request.make_response(rendu, headers=[
            ("Content-Type", "application/pdf" if genre == "pdf"
             else "text/html"),
            ("Content-Length", len(rendu)),
            ("Content-Disposition", content_disposition(nom)),
        ])

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
        # La règle de ce qui est servable vit sur le modèle (`_piece_publique`,
        # `_corps_a_rendre`) : elle s'y prouve sans serveur.
        # ⚠️ Le fichier d'une version se lit sur `latest_version_id` —
        # `current_version` est le NUMÉRO, un Char. Avec le mauvais nom, la
        # route levait un AttributeError et rendait 500 : le code QR d'une
        # étape adossée à une procédure n'ouvrait rien, alors que fichier et
        # adresse passaient.
        ressource = ressource.sudo()
        piece = ressource._piece_publique()
        if not piece:
            document = ressource._corps_a_rendre()
            if not document:
                return request.not_found()
            return self._corps_en_pdf(document)
        donnees = piece.raw
        return request.make_response(donnees, headers=[
            ("Content-Type", piece.mimetype or "application/octet-stream"),
            ("Content-Length", len(donnees)),
            ("Content-Disposition", content_disposition(piece.name)),
        ])
