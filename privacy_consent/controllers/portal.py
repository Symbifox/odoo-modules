from odoo import http, fields
from odoo.http import request
from odoo.addons.portal.controllers.portal import CustomerPortal


class PrivacyPortal(CustomerPortal):
    """Contrôleur portail pour la gestion des consentements.

    Fournit un accès authentifié (utilisateur portail) et public (par jeton)
    aux enregistrements de consentement.
    """

    def _get_consent_domain_for_partner(self, partner):
        """Domaine de recherche pour les consentements visibles par un contact.

        Retourne les consentements où le contact est :
        - le sujet direct, OU
        - un des responsables assignés (given_by_partner_ids), OU
        - un responsable légal du sujet mineur
        """
        minor_child_ids = partner.minor_child_ids.ids
        domain = [
            "|", "|",
            ("subject_partner_id", "=", partner.id),
            ("given_by_partner_ids", "in", partner.id),
            "&",
            ("subject_partner_id", "in", minor_child_ids),
            ("is_minor", "=", True),
        ]
        return domain

    def _can_access_consent(self, partner, consent):
        """Vérifier si le contact peut accéder à un consentement.

        Autorisé si le contact est le sujet, un des responsables assignés,
        ou un responsable légal du sujet mineur.
        """
        if not consent.exists():
            return False
        if consent.subject_partner_id.id == partner.id:
            return True
        if partner.id in consent.given_by_partner_ids.ids:
            return True
        if consent.is_minor and consent.subject_partner_id.legal_guardian_ids:
            if partner.id in consent.subject_partner_id.legal_guardian_ids.ids:
                return True
        return False

    def _prepare_home_portal_values(self, counters):
        values = super()._prepare_home_portal_values(counters)
        if "consent_count" in counters:
            partner = request.env.user.partner_id
            domain = self._get_consent_domain_for_partner(partner)
            values["consent_count"] = request.env["privacy.consent"].search_count(domain)
        return values

    @http.route(
        ["/my/privacy", "/my/privacy/preferences"],
        type="http",
        auth="user",
        website=True,
    )
    def portal_privacy_preferences(self, **kw):
        """Afficher et gérer les préférences de vie privée (Centre de préférences)."""
        partner = request.env.user.partner_id

        # Obtenir ou créer la préférence
        Preference = request.env["privacy.contact.preference"].sudo()
        preference = Preference.search([
            ("partner_id", "=", partner.id),
            ("company_id", "=", request.env.company.id),
        ], limit=1)

        if not preference:
            preference = Preference.create({
                "partner_id": partner.id,
            })

        # Obtenir les consentements (incluant ceux des enfants à charge)
        domain = self._get_consent_domain_for_partner(partner)
        consents = request.env["privacy.consent"].sudo().search(domain)

        # Obtenir les finalités
        purposes = request.env["privacy.purpose"].sudo().search([
            ("requires_consent", "=", True),
            ("active", "=", True),
        ])

        values = {
            "page_name": "privacy_preferences",
            "preference": preference,
            "consents": consents,
            "purposes": purposes,
            "partner": partner,
        }
        return request.render("privacy_consent.portal_privacy_preferences", values)

    @http.route(
        "/my/privacy/preferences/save",
        type="http",
        auth="user",
        website=True,
        methods=["POST"],
    )
    def portal_save_preferences(self, **kw):
        """Enregistrer les préférences de vie privée depuis le portail."""
        partner = request.env.user.partner_id

        Preference = request.env["privacy.contact.preference"].sudo()
        preference = Preference.search([
            ("partner_id", "=", partner.id),
            ("company_id", "=", request.env.company.id),
        ], limit=1)

        if not preference:
            preference = Preference.create({
                "partner_id": partner.id,
            })

        # Mettre à jour les préférences depuis le formulaire
        vals = {
            "allow_service_email": kw.get("allow_service_email") == "on",
            "allow_marketing_email": kw.get("allow_marketing_email") == "on",
            "allow_phone": kw.get("allow_phone") == "on",
            "allow_sms": kw.get("allow_sms") == "on",
            "do_not_contact": kw.get("do_not_contact") == "on",
        }

        if kw.get("preferred_language"):
            vals["preferred_language"] = kw.get("preferred_language")

        preference.write(vals)

        return request.redirect("/my/privacy/preferences?saved=1")

    @http.route(
        "/my/privacy/consents",
        type="http",
        auth="user",
        website=True,
    )
    def portal_privacy_consents(self, **kw):
        """Afficher l'historique des consentements."""
        partner = request.env.user.partner_id

        domain = self._get_consent_domain_for_partner(partner)
        consents = request.env["privacy.consent"].sudo().search(
            domain, order="create_date desc",
        )

        values = {
            "page_name": "privacy_consents",
            "consents": consents,
            "partner": partner,
        }
        return request.render("privacy_consent.portal_privacy_consents", values)

    @http.route(
        "/my/privacy/consent/<int:consent_id>",
        type="http",
        auth="user",
        website=True,
    )
    def portal_consent_detail(self, consent_id, **kw):
        """Afficher le détail d'un consentement."""
        partner = request.env.user.partner_id
        consent = request.env["privacy.consent"].sudo().browse(consent_id)

        if not self._can_access_consent(partner, consent):
            return request.redirect("/my/privacy/consents")

        values = {
            "page_name": "privacy_consent_detail",
            "consent": consent,
            "partner": partner,
            "is_public": False,
            "access_token": None,
        }
        return request.render("privacy_consent.portal_consent_detail", values)

    @http.route(
        "/my/privacy/consent/<int:consent_id>/respond",
        type="http",
        auth="user",
        website=True,
        methods=["POST"],
    )
    def portal_consent_respond(self, consent_id, **kw):
        """Traiter la réponse au consentement depuis le portail."""
        partner = request.env.user.partner_id
        consent = request.env["privacy.consent"].sudo().browse(consent_id)

        if not self._can_access_consent(partner, consent):
            return request.redirect("/my/privacy/consents")

        if consent.status != "pending":
            return request.redirect(f"/my/privacy/consent/{consent_id}")

        action = kw.get("action")

        # Si un responsable légal agit, s'assurer qu'il est dans given_by_partner_ids
        if consent.is_minor and partner.id not in consent.given_by_partner_ids.ids:
            consent.given_by_partner_ids = [(4, partner.id)]

        # Enregistrer la preuve avec données forensiques complètes
        Evidence = request.env["privacy.consent.evidence"].sudo()
        Evidence.create_from_http_request(
            consent=consent,
            action=action,
            note=f"Consentement {'accordé' if action == 'grant' else 'refusé'} via le portail authentifié par {partner.name}",
            request_obj=request,
            access_type="portal_authenticated",
        )

        if action == "grant":
            consent.action_grant()
        elif action == "refuse":
            consent.action_refuse()

        return request.redirect(f"/my/privacy/consent/{consent_id}?responded=1")

    @http.route(
        "/my/privacy/consent/<int:consent_id>/withdraw",
        type="http",
        auth="user",
        website=True,
        methods=["POST"],
    )
    def portal_consent_withdraw(self, consent_id, **kw):
        """Traiter le retrait de consentement depuis le portail."""
        partner = request.env.user.partner_id
        consent = request.env["privacy.consent"].sudo().browse(consent_id)

        if not self._can_access_consent(partner, consent):
            return request.redirect("/my/privacy/consents")

        # Seuls les consentements accordés peuvent être retirés
        if consent.status != "granted":
            return request.redirect(f"/my/privacy/consent/{consent_id}?error=cannot_withdraw")

        withdrawal_reason = kw.get("withdrawal_reason", "").strip()

        # Si un responsable légal agit, s'assurer qu'il est dans given_by_partner_ids
        if consent.is_minor and partner.id not in consent.given_by_partner_ids.ids:
            consent.given_by_partner_ids = [(4, partner.id)]

        # Enregistrer la preuve avec données forensiques complètes
        Evidence = request.env["privacy.consent.evidence"].sudo()
        note = f"Consentement retiré via le portail par {partner.name}"
        if withdrawal_reason:
            note += f". Raison : {withdrawal_reason}"

        Evidence.create_from_http_request(
            consent=consent,
            action="withdraw",
            note=note,
            request_obj=request,
            access_type="portal_authenticated",
        )

        # Mettre à jour le consentement
        consent.write({
            "status": "withdrawn",
            "withdrawn_at": fields.Datetime.now(),
            "withdrawal_reason": withdrawal_reason or "Retiré via le portail",
        })

        consent.message_post(
            body=f"Consentement retiré via le portail par {partner.name}. Raison : {withdrawal_reason or 'Non spécifiée'}",
            message_type="notification",
        )

        return request.redirect(f"/my/privacy/consent/{consent_id}?withdrawn=1")

    @http.route(
        "/my/privacy/consent/<int:consent_id>/renew",
        type="http",
        auth="user",
        website=True,
        methods=["POST"],
    )
    def portal_consent_renew(self, consent_id, **kw):
        """Traiter le renouvellement de consentement depuis le portail.

        Permet de renouveler les consentements accordés, expirés, refusés ou retirés.
        """
        partner = request.env.user.partner_id
        consent = request.env["privacy.consent"].sudo().browse(consent_id)

        if not self._can_access_consent(partner, consent):
            return request.redirect("/my/privacy/consents")

        # Peut renouveler les consentements accordés, expirés, refusés ou retirés
        if consent.status not in ("granted", "expired", "refused", "withdrawn"):
            return request.redirect(f"/my/privacy/consent/{consent_id}?error=cannot_renew")

        # Vérifier si déjà renouvelé
        if consent.renewed_to_id:
            return request.redirect(f"/my/privacy/consent/{consent.renewed_to_id.id}")

        # Créer le renouvellement
        try:
            new_consent = consent.copy({
                "status": "draft",
                "requested_at": False,
                "granted_at": False,
                "refused_at": False,
                "withdrawn_at": False,
                "expires_at": False,
                "docuseal_submission_id": False,
                "docuseal_status": False,
                "docuseal_sent_at": False,
                "docuseal_completed_at": False,
                "last_reminder_sent_at": False,
                "reminder_count": 0,
                "renewed_from_id": consent.id,
                "notes": f"Reconsentement via le portail le {fields.Date.today()} par {partner.name} (statut précédent : {consent.status})",
            })

            # Lier l'ancien consentement au nouveau
            consent.renewed_to_id = new_consent.id

            # Enregistrer la preuve avec données forensiques complètes
            Evidence = request.env["privacy.consent.evidence"].sudo()
            Evidence.create_from_http_request(
                consent=new_consent,
                action="renew",
                note=f"Reconsentement depuis le consentement #{consent.id} (était {consent.status}) via le portail par {partner.name}",
                request_obj=request,
                access_type="portal_authenticated",
            )

            # Accorder automatiquement le nouveau consentement
            new_consent.action_grant()

            consent.message_post(
                body=f"Consentement renouvelé via le portail par {partner.name}. Nouveau consentement : #{new_consent.id}",
                message_type="notification",
            )

            # Envoyer le courriel de confirmation de renouvellement
            template = request.env.ref(
                "privacy_consent.mail_template_consent_renewal_confirmation",
                raise_if_not_found=False,
            )
            if template:
                template.sudo().send_mail(new_consent.id, force_send=True)

            return request.redirect(f"/my/privacy/consent/{new_consent.id}?renewed=1")

        except Exception:
            return request.redirect(f"/my/privacy/consent/{consent_id}?error=renewal_failed")

    # -------------------------------------------------------------------------
    # Routes publiques (par jeton - aucune connexion requise)
    # -------------------------------------------------------------------------

    @http.route(
        "/privacy/consent/<int:consent_id>/<string:access_token>",
        type="http",
        auth="public",
        website=True,
    )
    def public_consent_detail(self, consent_id, access_token, **kw):
        """Accès public au détail du consentement par jeton (aucune connexion requise).

        C'est l'URL envoyée dans les notifications par courriel.
        """
        consent = request.env["privacy.consent"].sudo().browse(consent_id)

        # Valider le jeton
        if not consent.exists() or consent.access_token != access_token:
            return request.render("privacy_consent.portal_consent_invalid_token", {
                "page_name": "privacy_consent_error",
            })

        values = {
            "page_name": "privacy_consent_public",
            "consent": consent,
            "partner": consent.subject_partner_id,
            "access_token": access_token,
            "is_public": True,
        }
        return request.render("privacy_consent.portal_consent_detail", values)

    @http.route(
        "/privacy/consent/<int:consent_id>/<string:access_token>/respond",
        type="http",
        auth="public",
        website=True,
        methods=["POST"],
    )
    def public_consent_respond(self, consent_id, access_token, **kw):
        """Traiter la réponse au consentement depuis l'URL publique (aucune connexion requise)."""
        consent = request.env["privacy.consent"].sudo().browse(consent_id)

        # Valider le jeton
        if not consent.exists() or consent.access_token != access_token:
            return request.render("privacy_consent.portal_consent_invalid_token", {
                "page_name": "privacy_consent_error",
            })

        if consent.status != "pending":
            return request.redirect(f"/privacy/consent/{consent_id}/{access_token}?already_responded=1")

        action = kw.get("action")

        # Enregistrer la preuve avec données forensiques complètes (exigences judiciaires canadiennes)
        Evidence = request.env["privacy.consent.evidence"].sudo()
        Evidence.create_from_http_request(
            consent=consent,
            action=action,
            note=f"Consentement {'accordé' if action == 'grant' else 'refusé'} via le lien courriel public pour {consent.subject_partner_id.name}",
            request_obj=request,
            access_type="portal_public",
        )

        if action == "grant":
            consent.action_grant()
        elif action == "refuse":
            consent.action_refuse()

        return request.redirect(f"/privacy/consent/{consent_id}/{access_token}?responded=1")

    @http.route(
        "/privacy/consent/<int:consent_id>/<string:access_token>/withdraw",
        type="http",
        auth="public",
        website=True,
        methods=["POST"],
    )
    def public_consent_withdraw(self, consent_id, access_token, **kw):
        """Traiter le retrait de consentement depuis l'URL publique (aucune connexion requise)."""
        consent = request.env["privacy.consent"].sudo().browse(consent_id)

        # Valider le jeton
        if not consent.exists() or consent.access_token != access_token:
            return request.render("privacy_consent.portal_consent_invalid_token", {
                "page_name": "privacy_consent_error",
            })

        # Seuls les consentements accordés peuvent être retirés
        if consent.status != "granted":
            return request.redirect(f"/privacy/consent/{consent_id}/{access_token}?error=cannot_withdraw")

        withdrawal_reason = kw.get("withdrawal_reason", "").strip()

        # Enregistrer la preuve avec données forensiques complètes
        Evidence = request.env["privacy.consent.evidence"].sudo()
        note = f"Consentement retiré via le lien public pour {consent.subject_partner_id.name}"
        if withdrawal_reason:
            note += f". Raison : {withdrawal_reason}"

        Evidence.create_from_http_request(
            consent=consent,
            action="withdraw",
            note=note,
            request_obj=request,
            access_type="portal_public",
        )

        # Mettre à jour le consentement
        consent.write({
            "status": "withdrawn",
            "withdrawn_at": fields.Datetime.now(),
            "withdrawal_reason": withdrawal_reason or "Retiré via le lien public",
        })

        consent.message_post(
            body=f"Consentement retiré via le lien public. Raison : {withdrawal_reason or 'Non spécifiée'}",
            message_type="notification",
        )

        return request.redirect(f"/privacy/consent/{consent_id}/{access_token}?withdrawn=1")

    @http.route(
        "/privacy/consent/<int:consent_id>/<string:access_token>/renew",
        type="http",
        auth="public",
        website=True,
        methods=["POST"],
    )
    def public_consent_renew(self, consent_id, access_token, **kw):
        """Traiter le renouvellement de consentement depuis l'URL publique (aucune connexion requise)."""
        consent = request.env["privacy.consent"].sudo().browse(consent_id)

        # Valider le jeton
        if not consent.exists() or consent.access_token != access_token:
            return request.render("privacy_consent.portal_consent_invalid_token", {
                "page_name": "privacy_consent_error",
            })

        # Peut renouveler les consentements accordés, expirés, refusés ou retirés
        if consent.status not in ("granted", "expired", "refused", "withdrawn"):
            return request.redirect(f"/privacy/consent/{consent_id}/{access_token}?error=cannot_renew")

        # Vérifier si déjà renouvelé
        if consent.renewed_to_id:
            new_token = consent.renewed_to_id.access_token
            return request.redirect(f"/privacy/consent/{consent.renewed_to_id.id}/{new_token}")

        try:
            new_consent = consent.copy({
                "status": "draft",
                "requested_at": False,
                "granted_at": False,
                "refused_at": False,
                "withdrawn_at": False,
                "expires_at": False,
                "docuseal_submission_id": False,
                "docuseal_status": False,
                "docuseal_sent_at": False,
                "docuseal_completed_at": False,
                "last_reminder_sent_at": False,
                "reminder_count": 0,
                "renewed_from_id": consent.id,
                "notes": f"Reconsentement via le lien public le {fields.Date.today()} (statut précédent : {consent.status})",
            })

            # Lier l'ancien consentement au nouveau
            consent.renewed_to_id = new_consent.id

            # Enregistrer la preuve avec données forensiques complètes
            Evidence = request.env["privacy.consent.evidence"].sudo()
            Evidence.create_from_http_request(
                consent=new_consent,
                action="renew",
                note=f"Reconsentement depuis le consentement #{consent.id} (était {consent.status}) via le lien public",
                request_obj=request,
                access_type="portal_public",
            )

            # Accorder automatiquement le nouveau consentement
            new_consent.action_grant()

            consent.message_post(
                body=f"Consentement renouvelé via le lien public. Nouveau consentement : #{new_consent.id}",
                message_type="notification",
            )

            # Rediriger vers le nouveau consentement avec son jeton
            return request.redirect(f"/privacy/consent/{new_consent.id}/{new_consent.access_token}?renewed=1")

        except Exception:
            return request.redirect(f"/privacy/consent/{consent_id}/{access_token}?error=renewal_failed")
