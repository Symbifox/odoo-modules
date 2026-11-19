"""La page « Mes appareils » et les deux gestes qui la font vivre.

⚠️ **Portail, et pas une vue de l'application web.** Le geste se pose depuis le
téléphone qu'on vient de perdre de vue, souvent depuis un autre téléphone. Une
page de portail s'ouvre partout, sans charger l'interface complète, et elle
existe déjà pour les personnes externes le jour où un client en aura besoin.
"""
from odoo import http
from odoo.addons.portal.controllers.portal import CustomerPortal
from odoo.http import request


class PortailAppareils(CustomerPortal):

    def _prepare_home_portal_values(self, counters):
        valeurs = super()._prepare_home_portal_values(counters)
        if "appareils_count" in counters:
            valeurs["appareils_count"] = len(
                request.env["bf.device.registry"]._mes_appareils())
        return valeurs

    @http.route(["/my/appareils"], type="http", auth="user", website=True)
    def portail_appareils(self, **kw):
        appareils = request.env["bf.device.registry"]._mes_appareils()
        return request.render("bf_devices.portail_appareils", {
            "appareils": appareils,
            "page_name": "appareils",
            "retires": kw.get("retires"),
        })

    # ⚠️ POST et CSRF actif : une révocation déclenchée par une image dans un
    # courriel serait un déni de service silencieux sur les téléphones de la
    # personne. C'est aussi pourquoi ce n'est pas un lien.
    @http.route(["/my/appareils/retirer"], type="http", auth="user",
                methods=["POST"], website=True)
    def portail_retirer(self, cle=None, **kw):
        fait = request.env["bf.device.registry"]._revoquer(cle)
        return request.redirect("/my/appareils?retires=%s" % (1 if fait else 0))

    @http.route(["/my/appareils/tout-retirer"], type="http", auth="user",
                methods=["POST"], website=True)
    def portail_tout_retirer(self, **kw):
        retires = request.env["bf.device.registry"]._revoquer_tout()
        return request.redirect("/my/appareils?retires=%s" % retires)
