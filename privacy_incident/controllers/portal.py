from odoo import http
from odoo.exceptions import AccessError, MissingError
from odoo.http import request
from odoo.addons.portal.controllers.portal import CustomerPortal

# Champs qu'un client peut renseigner lui-même en déclarant un incident.
# Tout le reste — évaluation du risque, avis, mesures — relève du conseiller,
# et rien d'autre que cette liste n'est lu de la requête.
PORTAL_DECLARATION_FIELDS = (
    "title",
    "incident_type",
    "circumstances",
    "occurrence_date",
    "awareness_date",
    "pi_description",
    "subject_count",
)

INCIDENT_TYPES = (
    ("unauthorized_access", "Un accès non autorisé"),
    ("unauthorized_use", "Une utilisation non autorisée"),
    ("unauthorized_disclosure", "Une communication non autorisée"),
    ("loss", "Une perte ou une autre atteinte"),
)


class PrivacyIncidentPortal(CustomerPortal):
    """Surface client du registre des incidents de confidentialité.

    Volontairement sans entrée sur `/my` : ouvrir le portail aux clients est une
    décision de l'instance, qui pose elle-même son point d'entrée, plutôt que d'être
    prise ici par effet de bord.
    """

    def _incident_domain(self):
        partner = request.env.user.partner_id.commercial_partner_id
        if not partner:
            return [(0, "=", 1)]
        return [("partner_id", "=", partner.id)]

    @http.route(["/my/incidents"], type="http", auth="user", website=True)
    def portal_incident_list(self, **kw):
        Incident = request.env["privacy.incident"]
        incidents = Incident.search(self._incident_domain())
        values = {
            "page_name": "privacy_incident",
            "incidents": incidents,
            "partner": request.env.user.partner_id.commercial_partner_id,
        }
        return request.render("privacy_incident.portal_incident_list", values)

    @http.route(
        ["/my/incidents/<int:incident_id>"], type="http", auth="user", website=True
    )
    def portal_incident_detail(self, incident_id, **kw):
        # Pas de sudo : la règle d'enregistrement du portail borne déjà la
        # lecture à l'organisation de l'usager. Un identifiant qui n'est pas
        # le sien lève simplement une erreur d'accès.
        try:
            incident = request.env["privacy.incident"].browse(incident_id)
            incident.check_access("read")
            incident.read(["id"])
        except (AccessError, MissingError):
            return request.redirect("/my")
        return request.render(
            "privacy_incident.portal_incident_detail",
            {"page_name": "privacy_incident", "incident": incident},
        )

    @http.route(
        ["/my/incidents/declarer"],
        type="http",
        auth="user",
        website=True,
        methods=["GET", "POST"],
        csrf=True,
    )
    def portal_incident_declare(self, **post):
        partner = request.env.user.partner_id.commercial_partner_id
        errors = {}

        if request.httprequest.method == "POST":
            values = {
                key: post.get(key)
                for key in PORTAL_DECLARATION_FIELDS
                if post.get(key)
            }
            if not values.get("title"):
                errors["title"] = "Un objet est requis."
            if not values.get("awareness_date"):
                errors["awareness_date"] = (
                    "La date à laquelle vous avez pris connaissance de "
                    "l'incident est requise."
                )
            if values.get("subject_count"):
                try:
                    values["subject_count"] = int(values["subject_count"])
                except ValueError:
                    errors["subject_count"] = "Indiquez un nombre."
                    values.pop("subject_count")

            if not errors:
                if not partner:
                    return request.redirect("/my")
                values.update(
                    {
                        "partner_id": partner.id,
                        "company_id": request.env.company.id,
                        "state": "draft",
                        "declared_by_portal": True,
                        "declared_by_partner_id": request.env.user.partner_id.id,
                    }
                )
                # Création en sudo : un usager portail n'a pas le droit de
                # créer sur le modèle, et c'est voulu. L'organisation et la
                # provenance sont imposées ici, jamais lues de la requête.
                incident = request.env["privacy.incident"].sudo().create(values)
                incident.sudo().message_post(
                    body=(
                        "Incident déclaré depuis le portail par "
                        f"{request.env.user.partner_id.display_name}."
                    )
                )
                return request.redirect(f"/my/incidents/{incident.id}")

        return request.render(
            "privacy_incident.portal_incident_declare",
            {
                "page_name": "privacy_incident",
                "incident_types": INCIDENT_TYPES,
                "errors": errors,
                "post": post,
            },
        )
