"""La page de la tablette (publique, protégée par le jeton) et le consentement au portail."""
import logging

from markupsafe import Markup

from odoo import http
from odoo.http import request

from odoo.addons.bf_property_access_portal.controllers.portal import PropertyAccessPortal

_logger = logging.getLogger(__name__)


class LobbyController(http.Controller):

    def _kiosk(self, kiosk_id, token):
        kiosk = request.env["bf.property.lobby.kiosk"]._bf_from_token(kiosk_id, token)
        if kiosk:
            # Pages et réponses dans la langue de la société de l'immeuble, jamais
            # celle du navigateur de la tablette.
            lang = kiosk.company_id.partner_id.lang or "fr_CA"
            request.update_context(lang=lang)
            # L'enregistrement garde le contexte de sa lecture : le lui donner aussi.
            kiosk = kiosk.with_context(lang=lang)
        return kiosk

    @http.route(["/lobby/<int:kiosk_id>/<string:token>"], type="http", auth="public",
                methods=["GET"], sitemap=False)
    def lobby_page(self, kiosk_id, token, **kw):
        kiosk = self._kiosk(kiosk_id, token)
        if not kiosk:
            return request.not_found()
        response = request.render("bf_property_lobby.lobby_page", {
            "kiosk": kiosk, "directory": kiosk._bf_directory(), "doctype": Markup("<!DOCTYPE html>"),
            "base": "/lobby/%d/%s" % (kiosk.id, token)})
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        return response

    @http.route(["/lobby/<int:kiosk_id>/<string:token>/call"], type="json", auth="public",
                methods=["POST"], csrf=False)
    def lobby_call(self, kiosk_id, token, resident_id=None, **kw):
        kiosk = self._kiosk(kiosk_id, token)
        if not kiosk:
            return {"status": "refused", "message": ""}
        try:
            resident_id = int(resident_id)
            if not 0 < resident_id < 2 ** 31:
                raise ValueError
        except (TypeError, ValueError, OverflowError):
            return {"status": "refused", "message": ""}
        try:
            status, message = kiosk._bf_call(resident_id)
        except Exception:  # noqa: BLE001 — jamais de pile au public, mais au journal
            _logger.exception("Tablette %s : demande en échec", kiosk_id)
            return {"status": "refused", "message": ""}
        return {"status": status, "message": message}

    @http.route(["/lobby/<int:kiosk_id>/<string:token>/delivery"], type="json", auth="public",
                methods=["POST"], csrf=False)
    def lobby_delivery(self, kiosk_id, token, **kw):
        kiosk = self._kiosk(kiosk_id, token)
        if not kiosk:
            return {"status": "refused", "message": ""}
        try:
            status, message = kiosk._bf_delivery()
        except Exception:  # noqa: BLE001 — jamais de pile au public, mais au journal
            _logger.exception("Tablette %s : demande en échec", kiosk_id)
            return {"status": "refused", "message": ""}
        return {"status": status, "message": message}


class LobbyPortal(PropertyAccessPortal):

    def _lobby_residents(self):
        """Les fiches de résident de la personne, dans un immeuble qui a une tablette."""
        partner = request.env.user.partner_id
        residents = request.env["bf.property.access.resident"].sudo().search([
            ("partner_id", "=", partner.id), ("state", "in", ("active", "departure_pending"))])
        kiosks = request.env["bf.property.lobby.kiosk"].sudo().search(
            [("building_id", "in", residents.building_id.ids)])
        # Une tablette archivée n'empêche pas de se retirer de son annuaire.
        return residents.filtered(lambda r: r.building_id in kiosks.building_id or r.lobby_listed)

    @http.route(["/my/property/access"], type="http", auth="user", website=True)
    def portal_property_access(self, **kw):
        response = super().portal_property_access(**kw)
        if hasattr(response, "qcontext"):
            response.qcontext["lobby_residents"] = self._lobby_residents()
            response.qcontext["lobby_retention"] = {
                r.id: r.organisation_id.access_log_retention_days
                for r in response.qcontext["lobby_residents"]}
        return response

    @http.route(["/my/property/access/lobby"], type="http", auth="user", website=True,
                methods=["POST"], csrf=True)
    def portal_property_access_lobby(self, resident_id=None, listed=None, label=None,
                                     reachable=None, **kw):
        mine = self._lobby_residents()
        resident = mine.filtered(lambda r: str(r.id) == str(resident_id))
        if resident:
            resident._bf_set_lobby(bool(listed), label, bool(reachable))
        return request.redirect("/my/property/access")
