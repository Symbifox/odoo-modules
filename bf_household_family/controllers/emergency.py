"""La page publique de la fiche d'urgence, ouverte par le lien d'une gardienne.

Un jeton inconnu, expiré ou retiré rend la MÊME page « introuvable », avec le même
code 404 : aucune réponse ne dit lequel des trois. La page est servie sans cache,
sans référent et en noindex ; elle ne porte aucun lien vers l'instance.
"""
from odoo import http
from odoo.http import request
from odoo.tools import format_datetime

from ..models.emergency import LINK_ROUTE

HEADERS = [
    ("Cache-Control", "no-store, max-age=0"),
    ("Pragma", "no-cache"),
    ("X-Robots-Tag", "noindex, nofollow"),
    ("Referrer-Policy", "no-referrer"),
    ("X-Frame-Options", "DENY"),
    ("X-Content-Type-Options", "nosniff"),
]


class HouseholdEmergencyController(http.Controller):

    @http.route(f"{LINK_ROUTE}<string:token>", type="http", auth="public", methods=["GET"],
                sitemap=False, csrf=False)
    def emergency_card(self, token, **kwargs):
        Link = request.env["bf.household.emergency.link"]
        lien = Link._bf_find_valid(token)
        if not lien:
            # Aucune fiche, donc aucune langue de titulaire : celle du navigateur, si elle
            # est installée (un code inconnu ferait lever le rendu, sans nos en-têtes).
            return self._render("bf_household_family.emergency_public_not_found", {}, 404,
                                self._langue(request.best_lang))
        carte = lien.sudo().card_id
        proprietaire = carte.child_id.primary_parent_id or carte.holder_user_id
        # La langue du titulaire si elle est installée, sinon celle du foyer : jamais un
        # texte dans une langue et une date dans une autre (constaté le 2026-10-10).
        lang = self._langue(proprietaire.lang)
        lien._bf_record_opening()
        carte = carte.with_context(lang=lang)
        # En toutes lettres, dans la langue et le fuseau du titulaire : « 11 octobre 2026, 15:49 ».
        expire = format_datetime(carte.env, lien.expires_at, tz=proprietaire.tz or "UTC",
                                 dt_format="d MMMM y, HH:mm", lang_code=lang)
        valeurs = {
            "values": carte._bf_public_values(),
            "expires_label": expire,
        }
        return self._render("bf_household_family.emergency_public_page", valeurs, 200, lang)

    @staticmethod
    def _langue(code):
        installees = [c for c, _nom in request.env["res.lang"].get_installed()]
        if code in installees:
            return code
        return "en_US" if "en_US" in installees else (installees[0] if installees else "en_US")

    @staticmethod
    def _render(template, values, status, lang="en_US"):
        html = request.env["ir.qweb"].sudo().with_context(lang=lang)._render(
            template, dict(values, lang=lang.replace("_", "-")))
        return request.make_response(
            html, headers=[("Content-Type", "text/html; charset=utf-8"), *HEADERS], status=status)
