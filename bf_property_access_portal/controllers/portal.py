"""La page « Mes accès ».

La règle d'accès borne la recherche aux droits de la personne. Les noms
affichés (immeuble, fraction, point d'accès) sont lus en sudo, mais seulement
sur les droits que cette recherche a rendus : le portail n'a pas besoin de
lire les points d'accès ni l'immeuble pour savoir quelle porte il ouvre.
"""
import pytz

from odoo import fields, http
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.http import request

from odoo.addons.bf_property_portal.controllers.portal import PropertyPortal

STATE_ORDER = {"issued": 0, "to_issue": 1, "awaiting_consent": 2, "to_remove": 3}


class PropertyAccessPortal(PropertyPortal):

    def _prepare_home_portal_values(self, counters):
        values = super()._prepare_home_portal_values(counters)
        if "property_access_count" in counters:
            values["property_access_count"] = len(self._property_access_grants())
        return values

    def _property_to_utc(self, value):
        """L'heure postée est celle de l'immeuble quand le compte n'a pas de fuseau.

        Le socle prend alors UTC (« on ne devine pas ») : pour un visiteur attendu
        à 18 h, l'accès aurait expiré avant son arrivée. Ici
        le fuseau n'est pas deviné : c'est celui de la société, sinon de Montréal,
        où se trouvent les immeubles."""
        if request.env.user.tz:
            return super()._property_to_utc(value)
        tz = request.env.user.company_id.partner_id.tz or "America/Toronto"
        naive = fields.Datetime.to_datetime(value.replace("T", " "))
        return pytz.timezone(tz).localize(naive).astimezone(pytz.UTC).replace(tzinfo=None)

    def _property_access_domain(self):
        """« Mes accès » : ceux de la personne connectée, même pour un usager
        interne qui voit tout au back-office (un concierge qui habite
        l'immeuble). La règle portail reste la garde ; ce domaine borne le sens
        de la page."""
        return [("partner_id", "=", request.env.user.partner_id.id),
                ("state", "in", ("awaiting_consent", "to_issue", "issued", "to_remove"))]

    def _property_access_grants(self):
        """Les droits de la personne connectée.

        Cherchés en sudo avec EXACTEMENT le domaine de la règle portail (la
        personne, les états vivants), sous la garde de l'ACL : un résident dont
        l'immeuble est dans une autre société que son compte (le socle portail
        lit ses fractions en sudo pour la même raison) voit ses portes, et
        personne ne voit celles d'autrui."""
        Grant = request.env["bf.property.access.grant"]
        if not Grant.has_access("read"):
            return Grant
        return Grant.sudo().search(self._property_access_domain())

    def _property_access_rows(self):
        grants = self._property_access_grants()
        if not grants:
            return []
        Grant = request.env["bf.property.access.grant"]
        kinds = dict(Grant._fields["credential_kind"]._description_selection(request.env))
        states = dict(Grant._fields["state"]._description_selection(request.env))
        rows = []
        for grant in grants.sudo():
            rows.append({
                "building": grant.building_id.name,
                "unit": grant.unit_id.name or "",
                "point": grant.point_id.name,
                "kind": kinds.get(grant.credential_kind, ""),
                "ref": grant.external_ref or "",
                "state": grant.state,
                "state_label": states.get(grant.state, ""),
            })
        rows.sort(key=lambda r: (r["building"] or "", r["unit"], STATE_ORDER.get(r["state"], 9), r["point"] or ""))
        return rows

    @http.route(
        ["/my/property/visitors/new"], type="http", auth="user", website=True,
        methods=["POST"], csrf=True,
    )
    def portal_property_visit_create(self, **post):
        """Annoncer, puis, si l'hôte l'a demandé, donner l'accès temporaire.

        La visite naît par la route du socle ; on retrouve la sienne par son
        hôte (la plus récente, née dans cette requête)."""
        wants = post.pop("bf_access", False)
        until = post.pop("bf_access_until", "") or ""
        partner = request.env.user.partner_id
        Visit = request.env["bf.property.visit"].sudo()
        before = Visit.search([("host_partner_id", "=", partner.id)], order="id desc", limit=1).id
        response = super().portal_property_visit_create(**post)
        if wants:
            visit = Visit.search([("host_partner_id", "=", partner.id), ("id", ">", before or 0)],
                                 order="id desc", limit=1)
            if visit:
                try:
                    with request.env.cr.savepoint():
                        end = self._property_to_utc(until.strip()) if until.strip() else None
                        visit.with_user(request.env.user)._bf_request_access(end)
                except (AccessError, UserError, ValidationError, ValueError):
                    return request.redirect("/my/property/deliveries?error=access")
        return response

    @http.route(["/my/property/access"], type="http", auth="user", website=True)
    def portal_property_access(self, **kw):
        values = self._prepare_portal_layout_values()
        purpose = request.env.ref(
            "bf_property_access_privacy.purpose_property_access_control", raise_if_not_found=False
        )
        values.update({
            "page_name": "property_access",
            "rows": self._property_access_rows(),
            # L'avis d'information (art. 8 LPRPSP), quand le pont Loi 25 est là.
            "privacy_summary": purpose.sudo().plain_language_summary if purpose else False,
        })
        return request.render("bf_property_access_portal.portal_property_access", values)
