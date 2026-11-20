"""Le résident annonce le colis qu'il attend.

C'est lui qui connaît le numéro de suivi : il l'a dans son courriel de
confirmation. Personne au bureau n'a à le deviner, et le logiciel n'a aucun
moyen de le trouver — les API des transporteurs répondent sur un numéro, elles
ne disent pas ce qui arrive à une adresse.

⚠️ Les gardes du journal des colis s'appliquent telles quelles : la création
vérifie que la personne habite bien là (``_check_person_is_here``), et la
règle d'accès ne lui montre que les siens. Ce contrôleur n'ajoute donc qu'une
chose : il ne laisse écrire QUE les champs d'une annonce, et il fixe lui-même
l'état et le destinataire.
"""
import logging

from datetime import timedelta

from psycopg2 import IntegrityError

from odoo import _, fields, http
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.http import request

from odoo.addons.bf_property_portal.controllers.portal import PropertyPortal

from ..models.bf_property_carrier_account import CARRIERS

_logger = logging.getLogger(__name__)

MAX_NUMBER = 64
KINDS = ("parcel", "letter", "bulky", "perishable")
# Ce qu'une personne peut annoncer en une heure. Au-delà, ce n'est plus
# quelqu'un qui attend des colis.
MAX_PAR_HEURE = 12


class BfPropertyParcelTrackingPortal(PropertyPortal):

    @http.route(["/my/property/deliveries"], type="http", auth="user", website=True)
    def portal_property_deliveries(self, **kw):
        """La même page, plus ce qu'il faut pour annoncer un colis."""
        response = super().portal_property_deliveries(**kw)
        context = getattr(response, "qcontext", None)
        if context is None:
            return response
        # ⚠️ Purolator n'est pas branché : l'offrir au résident, c'est lui
        # promettre un suivi qui finira « à suivre à la main » sans qu'il sache
        # pourquoi (la page ne montre pas le refus). Il reste saisissable au
        # bureau, où la fiche dit ce qui manque.
        context["parcel_carriers"] = [
            (code, libelle) for code, libelle in CARRIERS if code != "purolator"
        ] + [("other", _("Autre transporteur"))]
        # ⚠️ Les deux listes sont séparées : un colis annoncé n'a ni date de
        # réception ni endroit où il est rangé, et il paraîtrait vide dans le
        # tableau « ce qui vous attend à la réception ».
        parcels = context.get("parcels")
        if parcels is not None:
            context["expected_parcels"] = parcels.filtered(
                lambda p: p.state == "expected")
            context["parcels"] = parcels - context["expected_parcels"]
        return response

    @http.route(
        ["/my/property/parcels/announce"],
        type="http", auth="user", website=True, methods=["POST"], csrf=True,
    )
    def portal_property_parcel_announce(self, **post):
        partner = request.env.user.partner_id
        units = request.env["bf.property.unit"].sudo()
        owned, occupied = units._portal_units_for(partner)
        mine = owned | occupied
        unit = mine.filtered(lambda u: str(u.id) == (post.get("unit_id") or ""))
        syndicats = mine.mapped("organisation_id")
        if not unit and len(syndicats) != 1:
            return request.redirect("/my/property/deliveries?error=unit")
        syndicat = unit.organisation_id if unit else syndicats

        number = (post.get("tracking_number") or "").strip()[:MAX_NUMBER]
        carrier = post.get("tracking_carrier") or ""
        if not number or carrier not in dict(CARRIERS + [("other", "")]):
            return request.redirect("/my/property/deliveries?error=parcel")
        kind = post.get("parcel_type") if post.get("parcel_type") in KINDS else "parcel"
        # 🔴 Le double-clic, le rafraîchissement, ou le conjoint qui a déjà
        # annoncé le même numéro : la contrainte d'unicité lèverait une
        # `IntegrityError` que le `except` ne connaît pas, et le résident
        # recevrait une page d'erreur portant le texte de la contrainte — qui
        # lui apprend au passage qu'un voisin a annoncé ce numéro.
        if request.env["bf.property.parcel"].sudo().search_count([
            ("company_id", "=", syndicat.company_id.id),
            ("tracking_carrier", "=", carrier),
            ("tracking_number", "=", number),
        ]):
            return request.redirect("/my/property/deliveries?error=double")
        recentes = request.env["bf.property.parcel"].sudo().search_count([
            ("partner_id", "=", partner.id),
            ("state", "=", "expected"),
            ("create_date", ">=", fields.Datetime.now() - timedelta(hours=1)),
        ])
        if recentes >= MAX_PAR_HEURE:
            # 🔴 Sans ce plafond, quelques milliers d'annonces d'une seule
            # personne prenaient toute la file d'interrogation : le colis du
            # voisin n'était plus jamais interrogé, sans message ni alerte.
            _logger.info("Annonces de colis plafonnées pour le partenaire %s",
                         partner.id)
            return request.redirect("/my/property/deliveries?error=trop")
        try:
            with request.env.cr.savepoint():
                request.env["bf.property.parcel"].create({
                    "organisation_id": syndicat.id,
                    "building_id": unit.building_id.id if unit else False,
                    "unit_id": unit.id if unit else False,
                    # Le résident s'annonce son propre colis : rien d'autre
                    # n'est écrivable, et surtout pas le destinataire.
                    "partner_id": partner.id,
                    "parcel_type": kind,
                    "state": "expected",
                    "tracking_carrier": carrier,
                    "tracking_number": number,
                })
        except (AccessError, ValidationError, UserError, ValueError, TypeError,
                IntegrityError):
            # La raison exacte reste au journal du serveur : la page ne dit pas
            # à un occupant ce que les règles d'accès cachent.
            _logger.info("Annonce de colis refusée pour le partenaire %s",
                         partner.id)
            return request.redirect("/my/property/deliveries?error=parcel")
        return request.redirect("/my/property/deliveries")

    @http.route(
        ["/my/property/parcels/<int:parcel_id>/cancel"],
        type="http", auth="user", website=True, methods=["POST"], csrf=True,
    )
    def portal_property_parcel_cancel(self, parcel_id, **post):
        """Le résident retire une annonce. La règle d'accès ne lui laisse
        effacer que les SIENNES, et seulement tant qu'elles sont annoncées."""
        parcel = request.env["bf.property.parcel"].search(
            [("id", "=", parcel_id), ("state", "=", "expected")], limit=1)
        if parcel:
            try:
                with request.env.cr.savepoint():
                    parcel.action_cancel_announcement()
            except (AccessError, UserError):
                _logger.info("Annulation d'annonce refusée pour le partenaire %s",
                             request.env.user.partner_id.id)
        return request.redirect("/my/property/deliveries")
