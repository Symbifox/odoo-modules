# -*- coding: utf-8 -*-
"""La page que le client hébergé ouvre sans compte : `/etat/<client>/<jeton>`.

Toutes les gardes répondent la même chose (404) : client absent, archivé ou
simple contact d'une société, page non publiée, jeton faux, vide ou hors forme.
Distinguer les cas renseignerait un visiteur sur l'existence d'un client.

⚠️ Le jeton voyage dans le chemin, et la page cite les adresses des services du
client : `Referrer-Policy: no-referrer` empêche qu'un lien sortant le transmette.
`X-Robots-Tag` la tient hors des moteurs, `Cache-Control: no-store` hors des
caches partagés. Le contrôleur est la seule porte : aucune ACL portail n'existe
sur `hosting.*`, il lit en sudo et ne passe au gabarit qu'un dictionnaire.
"""
import logging

from werkzeug.exceptions import NotFound

from odoo import http
from odoo.http import request

from ..models.res_partner import jetons_egaux

_logger = logging.getLogger(__name__)

EN_TETES = [
    ("Referrer-Policy", "no-referrer"),
    ("X-Robots-Tag", "noindex, nofollow"),
    ("Cache-Control", "no-store"),
]


def refus():
    """Le même 404 pour toutes les portes fermées, avec les mêmes en-têtes."""
    reponse = NotFound().get_response()
    for nom, valeur in EN_TETES:
        reponse.headers[nom] = valeur
    return reponse


class HostingStatusPage(http.Controller):

    @staticmethod
    def _hosting_status_client(partner_id, token):
        """Le client commercial en sudo si toutes les gardes passent, sinon None."""
        client = request.env["res.partner"].sudo().browse(partner_id).exists()
        if (not client or not client.active or not token
                or not client._hosting_status_est_commercial()
                or not client.hosting_status_published):
            return None
        if not jetons_egaux(client.hosting_status_token or "", token):
            return None
        return client

    @staticmethod
    def _hosting_status_langue(client):
        """La langue du client, posée sur la REQUÊTE : les libellés du gabarit la
        suivent, pas celle du navigateur."""
        langue = client.lang
        actives = [code for code, _nom in request.env["res.lang"].get_installed()]
        if langue in actives and request.env.context.get("lang") != langue:
            request.update_context(lang=langue)
        return client.with_context(lang=request.env.context.get("lang"))

    @http.route(
        ["/etat/<int:partner_id>/<string:token>"],
        # Pas website=True : la page est autonome (ni mise en page du site, ni
        # script de statistiques), et sans lui aucune redirection /<langue>/.
        type="http", auth="public", methods=["GET"], sitemap=False)
    def hosting_status_page(self, partner_id, token, **kw):
        client = self._hosting_status_client(partner_id, token)
        if client is None:
            return refus()
        client = self._hosting_status_langue(client)
        try:
            valeurs = client._hosting_status_valeurs()
            valeurs["company"] = client.company_id or request.env.company
        except Exception:
            # Route PUBLIQUE : une donnée aberrante ne rend pas une trace au visiteur.
            _logger.exception("Page d'état illisible pour le client %s", partner_id)
            return refus()
        return request.render("bf_hosting_status.status_page", valeurs, headers=EN_TETES)
