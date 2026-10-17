"""Portail de l'OCA ouvert par le jeton d'une réservation : renvoyé aux pages de ce module.

🔴 Le jeton d'une réservation (`access_token`) ouvre deux familles de
pages : celles de ce module (`/appointment/b/<id>/<jeton>`) et celles du portail
de `resource_booking` (`/my/bookings/<id>?access_token=<jeton>`). Les secondes
n'ont aucune des gardes des premières :

- la fiche montre le titre complet et les participants tels qu'ils sont au
  carnet, alors que le formulaire public retrouve la fiche par l'adresse TAPÉE,
  sans preuve que la personne la possède (voir `bf_page_title`) ;
- `/confirm` et `/cancel` agissent sur un simple GET (un antivirus de messagerie
  qui suit les liens suffit), sans le contrôle d'expiration du lien
  (`_link_is_usable`), sans les consentements demandés à l'écran, et avec les
  avis de l'OCA au lieu des courriels de ce module.

Quand le jeton est la SEULE chose qui ouvre la réservation à la personne, ces
routes renvoient donc à la page correspondante de ce module, sans rien faire
d'autre : ni lecture, ni confirmation, ni annulation. Le jeton n'est pas vérifié
ici, la page d'arrivée le vérifie (un jeton faux y mène à l'accueil).

Une personne connectée qui ouvre la réservation par ses propres droits (le
demandeur au portail, l'équipe) garde le portail de l'OCA, même quand le lien
porte le jeton : `get_portal_url()` l'ajoute à tous les liens du portail (liste,
boutons, formulaire de confirmation), et les renvoyer tous briserait son
parcours.
"""
from urllib.parse import quote

from odoo.exceptions import AccessError, MissingError
from odoo.http import request, route

from odoo.addons.resource_booking.controllers import portal


def _par_le_jeton_seulement(booking_id, access_token):
    """Vrai quand un jeton est donné et que la personne n'a pas d'accès propre."""
    if not access_token:
        return False
    booking = request.env["resource.booking"].browse(booking_id)
    try:
        booking.check_access("read")
    except (AccessError, MissingError):
        return True
    return not booking.exists()


def _page_du_module(booking_id, access_token, suite=""):
    return request.redirect(
        "/appointment/b/%d/%s%s" % (booking_id, quote(str(access_token), safe=""), suite),
        local=True,
    )


class BookingPortalParJeton(portal.CustomerPortal):

    @route()
    def portal_booking_page(self, booking_id, access_token=None, **kwargs):
        if _par_le_jeton_seulement(booking_id, access_token):
            return _page_du_module(booking_id, access_token)
        return super().portal_booking_page(booking_id, access_token=access_token, **kwargs)

    @route()
    def portal_booking_schedule(
        self, booking_id, access_token=None, year=None, month=None, error=None, **kwargs
    ):
        if _par_le_jeton_seulement(booking_id, access_token):
            suite = "/schedule"
            if year and month:
                suite += "/%d/%d" % (year, month)
            return _page_du_module(booking_id, access_token, suite)
        return super().portal_booking_schedule(
            booking_id, access_token=access_token, year=year, month=month, error=error, **kwargs
        )

    @route()
    def portal_booking_cancel(self, booking_id, access_token=None, **kwargs):
        # La page d'annulation du module DEMANDE (GET) ; seul son POST annule.
        if _par_le_jeton_seulement(booking_id, access_token):
            return _page_du_module(booking_id, access_token, "/cancel")
        return super().portal_booking_cancel(booking_id, access_token=access_token, **kwargs)

    @route()
    def portal_booking_confirm(self, booking_id, access_token=None, when=None, **kwargs):
        # Le créneau se confirme sur la page du module, par son POST.
        if _par_le_jeton_seulement(booking_id, access_token):
            return _page_du_module(booking_id, access_token)
        return super().portal_booking_confirm(booking_id, access_token, when, **kwargs)
