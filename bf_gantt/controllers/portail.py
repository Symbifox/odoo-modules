# -*- coding: utf-8 -*-
"""Ce qu'on ouvre sans avoir de compte.

C'est la raison d'être du module. Un échéancier se montre à des bénévoles, à un
comité, à un client : cent personnes qui n'auront jamais de siège. Le portail
sert la même géométrie que le back-office, en lecture seule, derrière un token.

Trois gardes, et les trois comptent :

1. le token, vérifié par `_document_check_access` du portail d'Odoo ;
2. le drapeau de publication, parce qu'un token qui existe n'est pas une
   permission de publier, et qu'il faut pouvoir refermer sans le changer ;
3. le même contrôle sur les fichiers que sur la page, sinon il suffirait de
   deviner l'adresse du PDF pour se passer du reste.
"""
import logging

from markupsafe import Markup

from odoo import _, http
from odoo.exceptions import AccessError, MissingError
from odoo.http import content_disposition, request

from odoo.addons.portal.controllers.portal import CustomerPortal

from ..models.gantt_export import FORMATS

_logger = logging.getLogger(__name__)

MODELES = {
    "project": ("project.project", "bf_gantt_published"),
    "plan": ("bf.gantt.plan", "portal_published"),
}


class EcheancierPortail(CustomerPortal):

    def _echeancier(self, kind, res_id, access_token):
        """L'enregistrement, si les trois gardes passent. Sinon, une erreur."""
        if kind not in MODELES:
            raise MissingError(_("Échéancier inconnu."))
        modele, drapeau = MODELES[kind]
        enregistrement = self._document_check_access(modele, res_id, access_token)
        if not enregistrement[drapeau]:
            # Le token est bon mais la publication est fermée. On refuse comme
            # si de rien n'était : distinguer les deux cas renseignerait un
            # visiteur sur l'existence de l'enregistrement.
            raise AccessError(_("Cet échéancier n'est pas publié."))
        return enregistrement

    def _payload(self, kind, enregistrement):
        """La même source que le back-office, lue en sudo APRÈS les gardes."""
        regroupement = "stage"
        if kind == "project":
            regroupement = enregistrement.bf_gantt_grouping or "stage"
        return request.env["bf.gantt.source"].sudo().get_echeancier(
            kind, enregistrement.id, grouping=regroupement)

    @http.route(
        ["/mon/echeancier/<string:kind>/<int:res_id>"],
        type="http", auth="public", website=True, sitemap=False)
    def page(self, kind, res_id, access_token=None, echelle="week", zoom=None,
             **kw):
        try:
            enregistrement = self._echeancier(kind, res_id, access_token)
        except (AccessError, MissingError):
            return request.redirect("/my")

        if echelle not in ("day", "week", "month"):
            echelle = "week"
        try:
            payload = self._payload(kind, enregistrement)
        except (AccessError, MissingError):
            return request.redirect("/my")
        except Exception:
            # ⚠️ Route PUBLIQUE : une donnée aberrante ne doit pas rendre un 500
            # au visiteur ni remplir le journal. On refuse comme le reste.
            _logger.exception("Échéancier %s/%s illisible", kind, res_id)
            return request.redirect("/my")

        from ..generateur import geometrie as geo
        from ..generateur import svg as gen_svg
        # Le tracé est calibré pour l'impression : à 1:1 il est illisible à
        # l'écran. Le portail ouvre donc à 150 %, et le facteur voyage dans
        # l'adresse, ce qui rend le lien partageable tel qu'on l'a réglé.
        facteur = geo.borner_zoom(zoom, defaut=geo.ZOOM_DEFAUT)
        dessin = gen_svg.rendre(payload, echelle=echelle,
                                zoom=facteur).decode("utf-8")
        # On retire la déclaration XML : le SVG est inséré dans du HTML, où elle
        # n'a pas sa place et où certains navigateurs la rendent en texte.
        dessin = Markup(dessin.split("?>", 1)[-1].lstrip())

        return request.render("bf_gantt.portail_echeancier", {
            "enregistrement": enregistrement,
            "kind": kind,
            "payload": payload,
            "dessin": dessin,
            "echelle": echelle,
            "zoom": facteur,
            # ⚠️ Le libellé est fabriqué ici, pas dans le gabarit : un `%` dans
            # une expression QWeb part en « incomplete format » à la compilation.
            "zooms": [(z, "%d %%" % round(z * 100)) for z in geo.ZOOMS_OFFERTS],
            "access_token": access_token,
            "formats": [("pdf", "PDF"), ("png", "PNG"), ("svg", "SVG"),
                        ("xlsx", "Excel"), ("mspdi", "MS Project")],
            "page_name": "echeancier",
        })

    @http.route(
        ["/mon/echeancier/<string:kind>/<int:res_id>/<string:format_>"],
        type="http", auth="public", website=False, sitemap=False)
    def fichier(self, kind, res_id, format_, access_token=None,
                echelle="week", zoom=None, **kw):
        if format_ not in FORMATS:
            raise MissingError(_("Format inconnu."))
        try:
            enregistrement = self._echeancier(kind, res_id, access_token)
        except (AccessError, MissingError):
            return request.redirect("/my")

        from ..generateur import geometrie as geo
        try:
            payload = self._payload(kind, enregistrement)
            contenu, mime, nom = request.env["bf.gantt.export"].sudo()._rendre(
                payload, format_,
                echelle=echelle if echelle in ("day", "week", "month") else "week",
                zoom=geo.borner_zoom(zoom, defaut=1.0))
        except (AccessError, MissingError):
            return request.redirect("/my")
        except Exception:
            _logger.exception("Échéancier %s/%s : rendu %s impossible",
                              kind, res_id, format_)
            return request.redirect("/my")

        return request.make_response(contenu, headers=[
            ("Content-Type", mime),
            ("Content-Length", len(contenu)),
            ("Content-Disposition", content_disposition(nom)),
            # Un échéancier bouge : on ne le laisse pas dormir dans un cache
            # intermédiaire, sinon le lecteur voit la version d'hier.
            ("Cache-Control", "no-store, max-age=0"),
        ])
