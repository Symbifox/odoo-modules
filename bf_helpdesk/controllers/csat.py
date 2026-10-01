from odoo import http
from odoo.http import request

from odoo.addons.bf_helpdesk.models.helpdesk_ticket_csat import (
    CES_EN,
    CES_SCALE,
    NEGATIVE_MAX,
    RATINGS,
    RATINGS_EN,
    REASONS,
    REASONS_EN,
    localized,
)


def _is_en(csat):
    lang = (csat.ticket_id.client_lang if csat else None) or request.env.lang or ""
    return lang.startswith("en")


class HelpdeskCsatController(http.Controller):
    """Page de réponse au sondage de satisfaction, ouverte par le jeton.

    Le GET ne fait qu'afficher : un filtre de liens qui ouvre le courriel
    n'enregistre rien. Seul le POST du formulaire (jeton CSRF) enregistre.
    """

    def _csat(self, token):
        if not token or len(token) > 64:
            return request.env["helpdesk.ticket.csat"]
        return request.env["helpdesk.ticket.csat"].sudo().search(
            [("token", "=", token)], limit=1)

    def _render(self, csat, **values):
        en = _is_en(csat)
        values.update({
            "csat": csat,
            "ticket": csat.ticket_id,
            "team": csat.ticket_id.team_id,
            "ratings": localized(RATINGS, RATINGS_EN, en),
            "reasons": localized(REASONS, REASONS_EN, en),
            "ces_scale": localized(CES_SCALE, CES_EN, en),
            "negative_max": NEGATIVE_MAX,
            "en": en,
        })
        return request.render("bf_helpdesk.csat_page", values)

    @http.route(
        ["/helpdesk/csat/<string:token>",
         "/helpdesk/csat/<string:token>/<int:score>"],
        type="http", auth="public", website=True, methods=["GET"],
        sitemap=False,
    )
    def csat_form(self, token, score=None, **kw):
        csat = self._csat(token)
        if not csat or not csat._is_open():
            return request.render("bf_helpdesk.csat_closed", {"csat": csat, "en": _is_en(csat)})
        selected = str(score) if score and str(score) in dict(RATINGS) else csat.rating
        return self._render(csat, selected=selected)

    @http.route(
        "/helpdesk/csat/<string:token>/submit",
        type="http", auth="public", website=True, methods=["POST"],
        csrf=True, sitemap=False,
    )
    def csat_submit(self, token, **kw):
        csat = self._csat(token)
        if not csat or not csat._is_open():
            return request.render("bf_helpdesk.csat_closed", {"csat": csat, "en": _is_en(csat)})
        rating = kw.get("rating")
        if rating not in dict(RATINGS):
            return self._render(
                csat, selected=None,
                error=("Choose a rating from 1 to 5 before sending." if _is_en(csat)
                       else "Choisissez une note de 1 à 5 avant d'envoyer."))
        csat._record_answer(
            rating,
            reason=kw.get("reason"),
            ces=kw.get("ces"),
            comment=kw.get("comment"),
        )
        return request.render("bf_helpdesk.csat_thanks", {
            "csat": csat, "negative": int(rating) <= NEGATIVE_MAX, "en": _is_en(csat),
        })
