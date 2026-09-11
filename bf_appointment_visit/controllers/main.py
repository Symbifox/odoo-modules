"""Les trois pages publiques des visites.

Aucune ne demande de compte. Un vendeur ne se connectera pas à un logiciel pour
dire oui à une visite, et quelqu'un qui arrive à une visite libre n'ouvrira pas
un portail sur son téléphone. Le jeton du lien est donc la seule authentification,
et chaque route est limitée en débit : une adresse de jeton se devine à la force
brute comme n'importe quel secret.
"""

import logging
from datetime import datetime

from odoo import _, fields, http
from odoo.http import request, route

from odoo.addons.bf_appointment.controllers.main import bf_rate_limit

_logger = logging.getLogger(__name__)

# Une décision de vendeur, une inscription sur place et une rétroaction sont des
# gestes rares. Le plafond sert contre le balayage de jetons, pas contre l'usage.
LIMITE_JETON = (30, 3600)


def _limite(seau):
    """Refuser poliment quand une adresse insiste trop sur des jetons."""
    maximum, fenetre = LIMITE_JETON
    return bf_rate_limit(seau, maximum, fenetre)


class VisitPublic(http.Controller):

    # ------------------------------------------------------------------
    # Outils
    # ------------------------------------------------------------------
    def _visit_by_token(self, champ, token):
        if not token or len(token) < 12:
            return request.env["bf.visit"]
        return request.env["bf.visit"].sudo().search(
            [(champ, "=", token)], limit=1
        )

    def _local(self, visite, moment):
        """L'heure telle que le vendeur la dit, pas telle que la base la range."""
        if not moment:
            return ""
        tz = visite.listing_id.tz_name
        local = fields.Datetime.context_timestamp(
            visite.with_context(tz=tz), moment
        )
        return local.strftime("%d/%m/%Y, %H:%M")

    def _etat_libelle(self, visite):
        return dict(
            visite._fields["state"].selection
        ).get(visite.state, visite.state)

    # ------------------------------------------------------------------
    # 1. La décision du vendeur
    # ------------------------------------------------------------------
    @route(
        "/visite/vendeur/<string:token>",
        type="http", auth="public", website=True, methods=["GET", "POST"],
        csrf=True, sitemap=False,
    )
    def visit_seller(self, token, **kwargs):
        if not _limite("visit_seller"):
            return request.render(
                "bf_appointment_visit.visit_seller_page",
                {"visit": request.env["bf.visit"], "token": token,
                 "message": _("Trop de tentatives. Reprenez dans une heure."),
                 "message_kind": "warning", "quand": "", "etat_libelle": ""},
            )
        visite = self._visit_by_token("seller_token", token)
        if not visite:
            return request.not_found()

        message = message_kind = None
        if request.httprequest.method == "POST":
            message, message_kind = self._trancher(visite, kwargs)

        return request.render(
            "bf_appointment_visit.visit_seller_page",
            {
                "visit": visite,
                "token": token,
                "quand": self._local(visite, visite.start),
                "etat_libelle": self._etat_libelle(visite),
                "message": message,
                "message_kind": message_kind,
            },
        )

    def _trancher(self, visite, kwargs):
        """Poser la décision du vendeur, et dire ce qui vient de se passer."""
        if visite.state != "requested":
            return (
                _("Cette demande a déjà été tranchée."),
                "info",
            )
        decision = kwargs.get("decision")
        note = (kwargs.get("seller_note") or "").strip()
        propose = (kwargs.get("proposed_start") or "").strip()
        valeurs = {}
        if note:
            valeurs["seller_note"] = note
        if propose:
            # `datetime-local` rend une heure LOCALE sans fuseau. La convertir
            # avec le fuseau de l'inscription, sinon la proposition se décale
            # de l'écart au temps universel, et personne ne comprend pourquoi.
            try:
                naif = datetime.strptime(propose, "%Y-%m-%dT%H:%M")
                import pytz
                tz = pytz.timezone(visite.listing_id.tz_name)
                valeurs["proposed_start"] = (
                    tz.localize(naif).astimezone(pytz.utc).replace(tzinfo=None)
                )
            except (ValueError, TypeError):
                return (_("L'heure proposée n'a pas été comprise."), "warning")
        if valeurs:
            visite.write(valeurs)

        if decision == "approve" and not propose:
            visite.action_approve()
            return (
                _("C'est accepté. Le visiteur vient d'en être informé."),
                "success",
            )
        if decision == "decline" or propose:
            visite.action_decline()
            if propose:
                return (
                    _("Votre proposition est partie au visiteur, et le créneau "
                      "demandé est rendu disponible."),
                    "success",
                )
            return (
                _("C'est refusé. Le visiteur vient d'en être informé, et le "
                  "créneau est rendu disponible."),
                "success",
            )
        return (_("Aucune décision n'a été lue."), "warning")

    # ------------------------------------------------------------------
    # 2. La feuille d'inscription sur place
    # ------------------------------------------------------------------
    @route(
        "/visite/accueil/<string:token>",
        type="http", auth="public", website=True, methods=["GET", "POST"],
        csrf=True, sitemap=False,
    )
    def visit_walkin(self, token, **kwargs):
        if not _limite("visit_walkin"):
            return request.not_found()
        inscription = request.env["bf.visit.listing"].sudo().search(
            [("walkin_token", "=", token), ("state", "=", "published")], limit=1
        )
        if not inscription:
            return request.not_found()

        message = message_kind = None
        termine = False
        if request.httprequest.method == "POST":
            nom = (kwargs.get("visitor_name") or "").strip()
            if not nom or not kwargs.get("consent"):
                message = _("Il manque votre nom, ou votre accord pour le registre.")
                message_kind = "warning"
            else:
                self._inscrire_sur_place(inscription, kwargs, nom)
                message = _(
                    "Merci, vous êtes inscrit. Un courtier vous accueille dans "
                    "un instant."
                )
                message_kind = "success"
                termine = True

        return request.render(
            "bf_appointment_visit.visit_walkin_page",
            {
                "listing": inscription,
                "token": token,
                "message": message,
                "message_kind": message_kind,
                "termine": termine,
            },
        )

    def _inscrire_sur_place(self, inscription, kwargs, nom):
        """Une arrivée constatée tout de suite, donc un registre fermé tout de suite.

        La personne est devant la porte : son arrivée n'est pas une prévision,
        c'est un fait. On l'horodate à la seconde, et le registre se ferme là
        comme pour n'importe quelle visite.
        """
        representation = kwargs.get("representation") or "unknown"
        if representation not in ("none", "broker", "unknown"):
            representation = "unknown"
        maintenant = fields.Datetime.now()
        visite = request.env["bf.visit"].sudo().create({
            "listing_id": inscription.id,
            "is_walkin": True,
            "state": "approved",
            "start": maintenant,
            "duration": inscription.visit_duration,
            "visitor_name": nom,
            "visitor_email": (kwargs.get("visitor_email") or "").strip() or False,
            "visitor_phone": (kwargs.get("visitor_phone") or "").strip() or False,
            "visitor_count": _entier(kwargs.get("visitor_count"), 1),
            "representation": representation,
            "representation_asked_at": (
                maintenant if representation != "unknown" else False
            ),
            "buyer_broker_name": (
                kwargs.get("buyer_broker_name") or ""
            ).strip() or False,
            "setup_done": True,
        })
        visite.action_mark_arrived()
        return visite

    # ------------------------------------------------------------------
    # 3. La rétroaction
    # ------------------------------------------------------------------
    @route(
        "/visite/retour/<string:token>",
        type="http", auth="public", website=True, methods=["GET", "POST"],
        csrf=True, sitemap=False,
    )
    def visit_feedback(self, token, **kwargs):
        if not _limite("visit_feedback"):
            return request.not_found()
        visite = self._visit_by_token("feedback_token", token)
        if not visite:
            return request.not_found()

        message = message_kind = None
        termine = bool(visite.feedback_received_at)
        if request.httprequest.method == "POST" and not termine:
            visite.write({
                "feedback_interest": kwargs.get("feedback_interest") or False,
                "feedback_price": kwargs.get("feedback_price") or False,
                "feedback_comment": (kwargs.get("feedback_comment") or "").strip(),
                "feedback_received_at": fields.Datetime.now(),
            })
            message = _("Merci, c'est noté.")
            message_kind = "success"
            termine = True
        elif termine:
            message = _("Votre impression est déjà enregistrée. Merci.")
            message_kind = "info"

        return request.render(
            "bf_appointment_visit.visit_feedback_page",
            {
                "visit": visite,
                "token": token,
                "message": message,
                "message_kind": message_kind,
                "termine": termine,
            },
        )


def _entier(valeur, defaut):
    try:
        nombre = int(valeur)
    except (TypeError, ValueError):
        return defaut
    return nombre if nombre > 0 else defaut
