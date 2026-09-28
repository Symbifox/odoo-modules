"""Les pages du portail, et la garde qui les tient.

⚠️ **Le contrôleur ne décide pas qui voit quoi.** Ce sont les règles d'accès
(`security/bf_property_portal_security.xml`) qui tranchent, et le contrôleur se
contente de chercher : si la recherche ne rend rien, la personne n'y a pas
droit. Écrire la garde deux fois, ici et là-bas, garantit surtout qu'un jour
les deux ne diront plus la même chose.

⚠️ **Une exception : la fenêtre d'affichage.** Elle dépend de la date du jour,
et une date n'a rien à faire dans le domaine d'une `ir.rule` : `ormcache` le
met en cache sans composante temporelle, l'évalue une fois et le gèle. C'est
donc ici, à chaque requête, que `is_visible_now` est appliqué.

⚠️ **Aucun lien anonyme.** Pas de `portal.mixin`, pas de jeton d'accès. Une
pièce du registre servie par un lien que l'on transfère donnerait à n'importe
qui ce que l'art. 1070.1 C.c.Q. réserve au copropriétaire, sous conditions. On
demande donc une session, toujours.
"""
import pytz

from markupsafe import Markup

from odoo import _, fields, http
from odoo.addons.portal.controllers.portal import CustomerPortal
from odoo.exceptions import AccessError, MissingError, UserError, ValidationError
from odoo.http import request

from odoo.addons.bf_property_core.models.mail_template import bf_mail_layout


class PropertyPortal(CustomerPortal):

    # ── Compteurs de la page d'accueil du portail ──

    def _prepare_home_portal_values(self, counters):
        values = super()._prepare_home_portal_values(counters)
        partner = request.env.user.partner_id
        if "property_announcement_count" in counters:
            values["property_announcement_count"] = request.env[
                "bf.property.announcement"
            ].search_count([("is_visible_now", "=", True)])
        if "property_request_count" in counters:
            values["property_request_count"] = request.env[
                "bf.property.request"
            ].search_count([("state", "not in", ("done", "refused"))])
        if "property_booking_count" in counters:
            values["property_booking_count"] = request.env[
                "bf.property.booking"
            ].search_count([("state", "in", ("requested", "confirmed"))])
        if "property_parcel_count" in counters:
            values["property_parcel_count"] = request.env[
                "bf.property.parcel"
            ].search_count([("state", "=", "held")])
        if "property_document_count" in counters:
            values["property_document_count"] = request.env[
                "bf.property.document"
            ].search_count([])
        if "property_unit_count" in counters:
            owned, occupied = request.env["bf.property.unit"].sudo()._portal_units_for(
                partner
            )
            values["property_unit_count"] = len(owned | occupied)
        return values

    # ── Pages ──

    @http.route(["/my/property"], type="http", auth="user", website=True)
    def portal_property_home(self, **kw):
        partner = request.env.user.partner_id
        units = request.env["bf.property.unit"].sudo()
        owned, occupied = units._portal_units_for(partner)
        roles = units._portal_organisations_for(partner)
        values = self._prepare_portal_layout_values()
        values.update(
            {
                "page_name": "property_home",
                "owned_units": owned,
                "occupied_units": occupied,
                "roles": roles,
                "announcements": request.env["bf.property.announcement"].search(
                    [("is_visible_now", "=", True)], limit=5
                ),
            }
        )
        return request.render("bf_property_portal.portal_property_home", values)

    @http.route(["/my/property/announcements"], type="http", auth="user", website=True)
    def portal_property_announcements(self, **kw):
        values = self._prepare_portal_layout_values()
        values.update(
            {
                "page_name": "property_announcements",
                # La fenêtre s'applique ICI, pas dans la règle d'accès.
                "announcements": request.env["bf.property.announcement"].search(
                    [("is_visible_now", "=", True)]
                ),
            }
        )
        return request.render(
            "bf_property_portal.portal_property_announcements", values
        )

    @http.route(["/my/property/documents"], type="http", auth="user", website=True)
    def portal_property_documents(self, **kw):
        documents = request.env["bf.property.document"].search([])
        values = self._prepare_portal_layout_values()
        values.update(
            {
                "page_name": "property_documents",
                "documents": documents,
                "register_items": documents.filtered("is_register_item"),
            }
        )
        return request.render("bf_property_portal.portal_property_documents", values)

    # ── Demandes d'entretien ──

    @http.route(["/my/property/requests"], type="http", auth="user", website=True)
    def portal_property_requests(self, **kw):
        values = self._prepare_portal_layout_values()
        values.update(
            {
                "page_name": "property_requests",
                "requests": request.env["bf.property.request"].search([]),
                "form_values": self._property_request_form_values(),
            }
        )
        return request.render("bf_property_portal.portal_property_requests", values)

    def _property_request_form_values(self):
        """Ce que le formulaire propose, borné à ce que la personne touche.

        🔴 Le formulaire ne fait que PROPOSER. Ce qui garde la création, c'est
        `bf.property.request.create()` côté serveur : un `unit_id` posté par un
        navigateur n'est pas une preuve de lien.
        """
        partner = request.env.user.partner_id
        units = request.env["bf.property.unit"].sudo()
        owned, occupied = units._portal_units_for(partner)
        mine = owned | occupied
        return {
            "units": mine,
            "syndicats": mine.mapped("organisation_id"),
            # 🔴 `_description_selection`, jamais `.selection` : la liste brute
            # porte les libellés SOURCE, et le formulaire anglais proposait
            # « Plomberie » et « Partie commune générale ».
            "categories": request.env["bf.property.request"]
            ._fields["category"]
            ._description_selection(request.env),
            "portions": [
                item
                for item in request.env["bf.property.request"]
                ._fields["portion_type"]
                ._description_selection(request.env)
                # L'occupant ne qualifie pas le régime des travaux : il décrit
                # ce qu'il voit. « À déterminer » est le défaut honnête.
                if item[0] in ("common", "restricted", "private", "unknown")
            ],
        }

    @http.route(
        ["/my/property/requests/new"],
        type="http",
        auth="user",
        website=True,
        methods=["POST"],
        csrf=True,
    )
    def portal_property_request_create(self, **post):
        partner = request.env.user.partner_id
        units = request.env["bf.property.unit"].sudo()
        owned, occupied = units._portal_units_for(partner)
        mine = owned | occupied

        unit = mine.filtered(lambda u: str(u.id) == (post.get("unit_id") or ""))
        if not unit:
            # Sans fraction nommée, on rattache au seul syndicat que la
            # personne touche ; s'il y en a plusieurs, on ne devine pas.
            syndicats = mine.mapped("organisation_id")
            if len(syndicats) != 1:
                return request.redirect("/my/property/requests?error=unit")
            syndicat = syndicats
            building = request.env["bf.property.building"]
        else:
            syndicat = unit.organisation_id
            building = unit.building_id

        description = (post.get("description") or "").strip()
        if not description:
            return request.redirect("/my/property/requests?error=description")

        values = {
            "organisation_id": syndicat.id,
            "building_id": building.id if building else False,
            "unit_id": unit.id if unit else False,
            "requester_partner_id": partner.id,
            "category": post.get("category") or "other",
            "portion_type": post.get("portion_type") or "unknown",
            "description": description,
            "is_safety": bool(post.get("is_safety")),
        }
        # ⚠️ PAS de `sudo` ici, et c'est délibéré. La garde du modèle
        # (`_check_requester_is_entitled`) ne s'applique qu'aux utilisateurs du
        # portail : créer en `sudo` la rendrait inerte et laisserait le
        # contrôleur seul juge. On crée donc avec les droits de la personne, et
        # les deux gardes jouent.
        # Même raison qu'à la réservation : une contrainte se déclenche au
        # vidage, et une demande refusée doit revenir en message plutôt qu'en
        # 500.
        try:
            with request.env.cr.savepoint():
                new_request = request.env["bf.property.request"].create(values)
        except (ValidationError, UserError):
            return request.redirect("/my/property/requests?error=refused")
        # ⚠️ `sudo` sur le SEUL message, et l'auteur reste la personne. Un
        # utilisateur du portail ne peut pas créer un `mail.message` de sa
        # propre autorité : sans cela le dépôt rend 403 avec « Type de
        # document : Message, Opération : create », alors même que la demande,
        # elle, s'était créée. L'attribution, elle, ne se sudoe pas : le fil
        # porte le nom du demandeur.
        self._property_tell_staff(
            new_request,
            new_request.description,
            (_("⚠️ Sécurité — nouvelle demande %s") if new_request.is_safety
             else _("Nouvelle demande %s")) % new_request.name,
        )
        return request.redirect("/my/property/requests")

    # ── Réservation des espaces communs ──

    @http.route(["/my/property/bookings"], type="http", auth="user", website=True)
    def portal_property_bookings(self, **kw):
        from datetime import timedelta

        partner = request.env.user.partner_id
        units = request.env["bf.property.unit"].sudo()
        owned, occupied = units._portal_units_for(partner)
        mine = owned | occupied

        areas = request.env["bf.property.common.area"].sudo().search(
            [
                ("bookable", "=", True),
                ("building_id", "in", mine.mapped("building_id").ids),
            ]
        )
        # ⚠️ Art. 1043 : une partie commune à usage restreint n'est pas à tous.
        areas = areas.filtered(
            lambda a: a.area_type != "restricted"
            or not a.restricted_unit_ids
            or bool(mine & a.restricted_unit_ids)
        )

        now = fields.Datetime.now()
        booking_model = request.env["bf.property.booking"]
        availability = [
            {
                "area": area,
                # ⚠️ Des créneaux, jamais des noms.
                "busy": booking_model._busy_slots(area, now, now + timedelta(days=30)),
            }
            for area in areas
        ]

        values = self._prepare_portal_layout_values()
        values.update(
            {
                "page_name": "property_bookings",
                "bookings": booking_model.search([]),
                "areas": areas,
                "units": mine,
                "availability": availability,
            }
        )
        return request.render("bf_property_portal.portal_property_bookings", values)

    @http.route(
        ["/my/property/bookings/new"],
        type="http",
        auth="user",
        website=True,
        methods=["POST"],
        csrf=True,
    )
    def portal_property_booking_create(self, **post):
        partner = request.env.user.partner_id
        units = request.env["bf.property.unit"].sudo()
        owned, occupied = units._portal_units_for(partner)
        mine = owned | occupied

        # ⚠️ La conversion est DANS le contrôle : postée à la main, une valeur
        # non numérique rendait une trace d'erreur au lieu d'une redirection, et
        # une page d'erreur en dit plus long qu'un message.
        try:
            area_id = int(post.get("common_area_id") or 0)
        except (TypeError, ValueError):
            return request.redirect("/my/property/bookings?error=area")
        area = request.env["bf.property.common.area"].sudo().browse(area_id).exists()
        if not area or not area.bookable or area.building_id not in mine.mapped(
            "building_id"
        ):
            return request.redirect("/my/property/bookings?error=area")

        start = (post.get("date_start") or "").strip()
        stop = (post.get("date_stop") or "").strip()
        if not start or not stop:
            return request.redirect("/my/property/bookings?error=window")
        try:
            # ⚠️ `datetime-local` poste l'heure LOCALE du navigateur, et Odoo
            # stocke en UTC. Lire la valeur telle quelle décalerait toute
            # réservation du fuseau de la personne : quatre heures à Montréal
            # l'été, et un samedi soir deviendrait un dimanche matin.
            date_start = self._property_to_utc(start)
            date_stop = self._property_to_utc(stop)
        except (ValueError, TypeError):
            return request.redirect("/my/property/bookings?error=window")

        unit = mine.filtered(lambda u: str(u.id) == (post.get("unit_id") or ""))
        try:
            # 🔴 Le savepoint n'est pas décoratif. Une `@api.constrains` se
            # déclenche au VIDAGE, pas au `create` : un `try` posé autour du
            # seul `create` la manque, la page redirige comme si tout allait
            # bien, et le chevauchement remonte plus tard. Le savepoint d'Odoo
            # vide en entrant ET en sortant, donc la contrainte joue là où on
            # peut l'attraper, et l'annulation ne laisse rien derrière.
            with request.env.cr.savepoint():
                booking = request.env["bf.property.booking"].create(
                    {
                        "common_area_id": area.id,
                        "partner_id": partner.id,
                        "unit_id": unit.id if unit else False,
                        "date_start": date_start,
                        "date_stop": date_stop,
                        "note": (post.get("note") or "").strip(),
                    }
                )
                self._property_tell_staff(
                    booking,
                    booking.note or _("Réservation déposée."),
                    _("Nouvelle réservation : %s") % booking.common_area_id.display_name,
                    syndicat=area.sudo().building_id.organisation_id,
                )
        except (ValidationError, UserError):
            # Le chevauchement, la durée, l'horizon et l'usage restreint
            # remontent tous ici : la page redit lequel plutôt qu'un 500.
            return request.redirect("/my/property/bookings?error=refused")
        return request.redirect("/my/property/bookings")

    def _property_tell_staff(self, record, body, subject, syndicat=None):
        """Le message du dépôt, au fil ET chez le bureau du syndicat.

        ⚠️ `sudo` sur le SEUL message et l'abonnement, et l'auteur reste la
        personne : un utilisateur du portail ne peut ni créer un
        `mail.message` ni abonner quelqu'un de sa propre autorité.
        """
        partner = request.env.user.partner_id
        syndicat = syndicat or record.sudo().organisation_id
        staff = syndicat._bf_portal_staff() if syndicat else request.env["res.partner"]
        staff -= partner
        target = record.sudo()
        if staff:
            target.message_subscribe(partner_ids=staff.ids)
        target.message_post(
            body=body,
            subject=subject,
            author_id=partner.id,
            message_type="comment",
            subtype_xmlid="mail.mt_comment",
            partner_ids=staff.ids,
        )
        self._property_email_inbox_staff(record, staff, subject, body)

    def _property_email_inbox_staff(self, record, staff, subject, body):
        """Le courriel EN PLUS de la boîte Odoo, selon la préférence de chacun.

        🔴 Une demande « ⚠️ Sécurité » restait dans la boîte Odoo du concierge
        jusqu'à ce qu'il l'ouvre. Défaut : la sécurité part aussi par
        courriel ; chaque personne règle ce choix dans ses préférences. Les
        comptes « courriel » l'ont déjà reçu par la notification ordinaire.
        """
        is_safety = bool(getattr(record, "is_safety", False))
        users = staff.sudo().user_ids.filtered(
            lambda u: u.active and not u.share and u.notification_type == "inbox"
            and (u.bf_property_portal_email == "all"
                 or (u.bf_property_portal_email in (False, "safety") and is_safety)))
        if not users:
            return
        link = "%s/mail/view?model=%s&res_id=%s" % (
            record.get_base_url(), record._name, record.id)
        # ⚠️ Un gabarit rendu dans la mise en page de marque, plus un HTML nu
        # (tous les courriels s'alignent sur l'habillage de la société). `sudo` : l'auteur du dépôt est un
        # utilisateur du portail, qui ne lit ni les gabarits ni la société.
        template = request.env.ref("bf_property_portal.mail_template_staff_alert").sudo()
        for user in users:
            template.with_context(
                lang=user.lang, bf_subject=subject, bf_body=body, bf_link=link,
            ).send_mail(user.id, email_layout_xmlid=bf_mail_layout(request.env))

    def _property_to_utc(self, value):
        """Une heure locale postée par le navigateur, rendue en UTC naïf.

        Sans fuseau sur l'utilisateur, on prend UTC : on ne devine pas.
        """
        naive = fields.Datetime.to_datetime(value.replace("T", " "))
        zone = pytz.timezone(request.env.user.tz or "UTC")
        return zone.localize(naive).astimezone(pytz.UTC).replace(tzinfo=None)

    @http.route(
        ["/my/property/bookings/<int:booking_id>/cancel"],
        type="http",
        auth="user",
        website=True,
        methods=["POST"],
        csrf=True,
    )
    def portal_property_booking_cancel(self, booking_id, **post):
        booking = request.env["bf.property.booking"].search(
            [("id", "=", booking_id)], limit=1
        )
        if booking and booking.state in ("requested", "confirmed"):
            booking.action_cancel()
        return request.redirect("/my/property/bookings")

    # ── Colis et visiteurs ──

    @http.route(["/my/property/deliveries"], type="http", auth="user", website=True)
    def portal_property_deliveries(self, **kw):
        partner = request.env.user.partner_id
        units = request.env["bf.property.unit"].sudo()
        owned, occupied = units._portal_units_for(partner)
        mine = owned | occupied
        values = self._prepare_portal_layout_values()
        values.update(
            {
                "page_name": "property_deliveries",
                # Les règles d'accès bornent déjà chacun aux siens.
                "parcels": request.env["bf.property.parcel"].search([]),
                "visits": request.env["bf.property.visit"].search([]),
                "units": mine,
                "syndicats": mine.mapped("organisation_id"),
                "visitor_kinds": request.env["bf.property.visit"]
                ._fields["visitor_kind"]
                ._description_selection(request.env),
            }
        )
        return request.render("bf_property_portal.portal_property_deliveries", values)

    @http.route(
        ["/my/property/visitors/new"],
        type="http",
        auth="user",
        website=True,
        methods=["POST"],
        csrf=True,
    )
    def portal_property_visit_create(self, **post):
        partner = request.env.user.partner_id
        units = request.env["bf.property.unit"].sudo()
        owned, occupied = units._portal_units_for(partner)
        mine = owned | occupied
        unit = mine.filtered(lambda u: str(u.id) == (post.get("unit_id") or ""))
        syndicats = mine.mapped("organisation_id")
        if not unit and len(syndicats) != 1:
            return request.redirect("/my/property/deliveries?error=unit")
        syndicat = unit.organisation_id if unit else syndicats

        name = (post.get("visitor_name") or "").strip()
        expected = (post.get("date_expected") or "").strip()
        if not name or not expected:
            return request.redirect("/my/property/deliveries?error=visitor")
        try:
            with request.env.cr.savepoint():
                visit = request.env["bf.property.visit"].create(
                    {
                        "organisation_id": syndicat.id,
                        "building_id": unit.building_id.id if unit else False,
                        "unit_id": unit.id if unit else False,
                        "host_partner_id": partner.id,
                        "visitor_name": name,
                        "visitor_kind": post.get("visitor_kind") or "guest",
                        "vehicle_plate": (post.get("vehicle_plate") or "").strip(),
                        "date_expected": self._property_to_utc(expected),
                    }
                )
                self._property_tell_staff(
                    visit,
                    _("Visiteur annoncé : %s") % visit.visitor_name,
                    _("Visiteur annoncé : %s") % visit.visitor_name,
                    syndicat=syndicat,
                )
        except (ValidationError, UserError, ValueError, TypeError):
            return request.redirect("/my/property/deliveries?error=visitor")
        return request.redirect("/my/property/deliveries")

    @http.route(
        ["/my/property/visitors/<int:visit_id>/cancel"],
        type="http",
        auth="user",
        website=True,
        methods=["POST"],
        csrf=True,
    )
    def portal_property_visit_cancel(self, visit_id, **post):
        visit = request.env["bf.property.visit"].search(
            [("id", "=", visit_id)], limit=1
        )
        if visit and visit.state in ("expected", "arrived"):
            visit.action_cancel()
        return request.redirect("/my/property/deliveries")

    @http.route(
        ["/my/property/document/<int:document_id>"],
        type="http",
        auth="user",
        website=True,
    )
    def portal_property_document(self, document_id, **kw):
        """Sert la pièce, après que les règles d'accès aient répondu.

        La recherche est faite SANS `sudo` : si la personne n'a pas droit à la
        pièce, elle ne la trouve pas, et le 404 est la bonne réponse. Ce n'est
        qu'ensuite que le fichier est lu en `sudo`, parce qu'une pièce jointe
        n'est pas rattachée à un enregistrement que le portail sait lire.
        """
        document = request.env["bf.property.document"].search(
            [("id", "=", document_id)], limit=1
        )
        if not document:
            return request.redirect("/my/property/documents")
        try:
            # seul le fichier rattaché à la pièce sort
            attachment = document._bf_portal_file()
            if not attachment:
                return request.redirect("/my/property/documents")
            stream = request.env["ir.binary"]._get_stream_from(attachment, "raw")
        except (AccessError, MissingError):
            return request.redirect("/my/property/documents")
        return stream.get_response(as_attachment=True)
