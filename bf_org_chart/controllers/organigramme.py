# -*- coding: utf-8 -*-
"""La page de l'organigramme, et son PDF.

Deux routes, une garde : le contrôleur ne dessine QUE des modèles qui ont
signé le contrat `bf.org.chart.source`, et seulement après avoir passé le
droit de lecture de l'utilisateur sur l'enregistrement demandé.

🔴 Sans ces deux contrôles, une route qui accepte un nom de modèle dans son
chemin est une lecture arbitraire de la base.
"""
import logging

from markupsafe import Markup

from odoo import http
from odoo.exceptions import AccessError, MissingError, UserError
from odoo.http import request
from ..moteur import svg as moteur_svg

_logger = logging.getLogger(__name__)


class Organigramme(http.Controller):

    def _source(self, modele, res_id):
        if modele not in request.env:
            raise request.not_found()
        enr = request.env[modele].browse(int(res_id))
        if not hasattr(enr, "_org_chart_carte"):
            raise request.not_found()
        try:
            enr.check_access("read")
        except (AccessError, MissingError):
            # Refus de droits et fiche disparue rendent la MÊME réponse : dire
            # laquelle des deux, c'est déjà dire si l'enregistrement existe.
            raise request.not_found()
        if not enr.exists():
            raise request.not_found()
        return enr

    def _genre_connu(self, enr, code):
        for genre in enr._org_chart_genres():
            if genre["code"] == code:
                return genre
        raise request.not_found()

    @http.route("/bf/organigramme/<string:modele>/<int:res_id>/<string:code>",
                type="http", auth="user", website=False)
    def page(self, modele, res_id, code, **kw):
        enr = self._source(modele, res_id)
        genre = self._genre_connu(enr, code)
        try:
            plan = enr._org_chart_plan(code)
            # 🔴 QWeb échappe toute chaîne : sans Markup, la page afficherait
            # le code SVG en clair au lieu du dessin.
            dessin = Markup(moteur_svg.rendre(plan))
            erreur = None
        except (AccessError, MissingError):
            # 🔴 En 18.0, AccessError et MissingError HÉRITENT de UserError :
            # un `except UserError` seul affichait un refus de droits comme un
            # avertissement de dessin, et le message étendu d'ir.rule nomme les
            # enregistrements refusés en mode développeur.
            raise request.not_found()
        except UserError as exc:
            plan, dessin = None, Markup("")
            erreur = exc.args[0] if exc.args else str(exc)
        html = request.env["ir.qweb"]._render("bf_org_chart.page", {
            "titre": (plan.titre if plan else genre["libelle"]),
            "genre": genre,
            "dessin": dessin,
            "erreur": erreur,
            "avertissements": plan.avertissements if plan else [],
            # Pas de bouton PDF sur une page qui vient d'échouer : il mènerait
            # à la même erreur, en 500 cette fois.
            "url_pdf": enr._org_chart_url(code, "pdf") if not erreur else "",
            "url_retour": "/odoo/%s/%s" % (modele.replace(".", "-"), enr.id),
        })
        return request.make_response(html, [("Content-Type", "text/html; charset=utf-8")])

    @http.route("/bf/organigramme/<string:modele>/<int:res_id>/<string:code>/pdf",
                type="http", auth="user", website=False)
    def fichier_pdf(self, modele, res_id, code, **kw):
        enr = self._source(modele, res_id)
        genre = self._genre_connu(enr, code)
        try:
            contenu = enr._org_chart_pdf(code)
        except (AccessError, MissingError):
            raise request.not_found()
        except UserError as exc:
            # La page rend l'erreur dans un encart; le fichier, lui, n'a pas
            # d'encart : il vaut mieux une 404 lisible qu'une 500 muette.
            raise request.not_found(exc.args[0] if exc.args else str(exc))
        nom = "%s - %s.pdf" % (
            (enr.display_name or "organigramme")[:60].replace("/", "-"),
            genre["libelle"])
        return request.make_response(contenu, [
            ("Content-Type", "application/pdf"),
            ("Content-Length", len(contenu)),
            ("Content-Disposition", http.content_disposition(nom)),
        ])
