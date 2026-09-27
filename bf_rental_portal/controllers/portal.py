"""Les pages du locataire, et ce qu'elles refusent de lui dire.

⚠️ **Le contrôleur ne décide pas qui voit quoi.** Ce sont les règles d'accès qui
tranchent, et le contrôleur cherche : si la recherche ne rend rien, la personne
n'y a pas droit. Même discipline que le portail de la copropriété.

⚠️ **Ce qui dépend du JOUR se calcule ici**, jamais dans une `ir.rule` : le
domaine d'une règle est mis en cache sans composante temporelle, donc une date y
serait évaluée une fois puis gelée.

🔴 **Ce que ces pages ne diront jamais au locataire.**

Elles ne lui annoncent pas qu'il « risque l'éviction ». Seul le tribunal résilie
(art. 1971, « peut obtenir »), le seuil de trois semaines ne touche qu'à la marge
du tribunal (art. 1973), et payer avant jugement arrête tout (art. 1883). Un
portail qui ferait peur dirait trois faussetés et n'aiderait personne.

Elles n'affichent pas non plus le **fondement** d'une résiliation de
l'art. 1974.1. Le champ est réservé à la gestion, donc inaccessible au portail —
mais même s'il ne l'était pas, il n'aurait rien à faire sur un écran qu'on
consulte dans une cuisine, devant qui que ce soit. Le locataire sait pourquoi il
part ; il n'a pas besoin de le lire en gros.

**Ce qu'elles disent, en revanche**, et qui coûte cher à ignorer : la date limite
de réponse à un avis, et ce que le silence produira. Un locataire qui laisse
passer un mois sur un avis de modification voit son bail reconduit avec tout ce
qui a été demandé (art. 1945). C'est l'information la plus chère du corpus, et
elle n'est nulle part ailleurs.
"""
from odoo import fields, http
from odoo.addons.portal.controllers.portal import CustomerPortal
from odoo.exceptions import AccessError, MissingError
from odoo.http import request


class RentalPortal(CustomerPortal):

    def _prepare_home_portal_values(self, counters):
        values = super()._prepare_home_portal_values(counters)
        if "rental_lease_count" in counters:
            values["rental_lease_count"] = request.env[
                "bf.rental.lease"
            ].search_count([])
        if "rental_notice_count" in counters:
            # ⚠️ Le compteur ne montre que ce qui APPELLE une réponse, pas tout
            # l'historique : un badge qui compte le passé ne se vide jamais et
            # cesse d'être lu.
            values["rental_notice_count"] = request.env[
                "bf.rental.notice"
            ].search_count([("state", "=", "given")])
        return values

    # ── Le bail ──

    @http.route(["/my/rental"], type="http", auth="user", website=True)
    def portal_rental_home(self, **kw):
        leases = request.env["bf.rental.lease"].search([])
        values = self._prepare_portal_layout_values()
        values.update({
            "page_name": "rental_leases",
            "leases": leases,
            # 🔴 Le gabarit ne touche PAS à `ir.attachment`. Le groupe Portail
            # n'y a aucun droit, et c'est voulu : l'ouvrir « pour afficher un
            # nom de fichier » ouvrirait toutes les pièces jointes de la base,
            # dont celles des avis de résiliation et des dossiers d'un autre.
            # La liste se construit donc ici, en `sudo()`, APRÈS que la
            # recherche ci-dessus a été faite sous les droits du lecteur : elle
            # ne peut porter que des baux qui sont les siens.
            "forms": {
                lease.id: [
                    {"id": att.id, "name": att.name}
                    for att in lease._bf_portal_signed_forms()
                ]
                for lease in leases
            },
        })
        return request.render("bf_rental_portal.portal_rental_leases", values)

    # ── Les avis, et surtout leurs échéances ──

    @http.route(["/my/rental/notices"], type="http", auth="user", website=True)
    def portal_rental_notices(self, **kw):
        notices = request.env["bf.rental.notice"].search([])
        today = fields.Date.context_today(request.env.user)
        # ⚠️ La sélection vit au MODÈLE, pas ici : une date dans une règle
        # d'accès serait gelée par le cache, et une sélection écrite au
        # contrôleur ne s'éprouverait qu'en cherchant des chaînes dans du HTML.
        pending = notices._portal_pending(notices)
        values = self._prepare_portal_layout_values()
        # ⚠️ Le SEUL délai du corpus qui joue contre le LOCATEUR (art. 1947
        # al. 2) : s'il omet de saisir le Tribunal dans le mois du refus, le
        # bail est reconduit aux conditions antérieures. Le locataire a tout
        # intérêt à le connaître, et personne d'autre ne le lui dira.
        refused = notices.filtered(
            lambda n: n.state == "refused" and n.landlord_tribunal_deadline
        )
        values.update({
            "page_name": "rental_notices",
            "notices": notices,
            "pending": pending,
            "refused": refused,
            "landlord_deadline": min(
                refused.mapped("landlord_tribunal_deadline"), default=False
            ),
            "today": today,
        })
        return request.render("bf_rental_portal.portal_rental_notices", values)

    # ── Le loyer ──

    @http.route(["/my/rental/rent"], type="http", auth="user", website=True)
    def portal_rental_rent(self, **kw):
        terms = request.env["bf.rental.term"].search([])
        values = self._prepare_portal_layout_values()
        values.update({
            "page_name": "rental_rent",
            "terms": terms,
            # 🔴 Ce que le locataire voit : ce qui reste dû, terme par terme.
            # PAS un total du bail — voir `models/bf_rental_term.py`.
            "outstanding": terms._portal_outstanding(terms),
            "currency": terms[:1].currency_id,
        })
        return request.render("bf_rental_portal.portal_rental_rent", values)

    # ── Le formulaire signé ──

    @http.route(["/my/rental/lease/<int:lease_id>/form/<int:attachment_id>"],
                type="http", auth="user", website=True)
    def portal_rental_lease_form(self, lease_id, attachment_id, **kw):
        """Le locataire retélécharge SON bail signé.

        ⚠️ Deux contrôles, pas un. La règle d'accès dit si le bail est le sien ;
        elle ne dit pas que la pièce demandée appartient à ce bail. Sans le
        second, un identifiant deviné servirait n'importe quelle pièce jointe de
        la base à qui a un bail quelconque.
        """
        try:
            lease = request.env["bf.rental.lease"].browse(lease_id)
            lease.check_access("read")
        except (AccessError, MissingError):
            return request.redirect("/my")
        attachment = lease._bf_portal_signed_forms().filtered(
            lambda a: a.id == attachment_id
        )
        if not attachment:
            return request.redirect("/my/rental")
        return request.env["ir.binary"]._get_stream_from(
            attachment, "raw"
        ).get_response(as_attachment=True)
