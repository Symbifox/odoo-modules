import logging
from datetime import timedelta

from odoo import http, fields
from odoo.exceptions import UserError
from odoo.http import request
from odoo.addons.portal.controllers.portal import CustomerPortal

from ..models.privacy_consent import PORTAL_RENEWABLE_STATES

_logger = logging.getLogger(__name__)

# Fenêtre de dédoublonnage de la preuve « view » de l'écran de renouvellement.
# ⚠ Ce GET est public : un rechargement, un retour arrière, un préchargement du
# navigateur ou un POST refusé faute de case cochée (il redirige vers ce même
# GET) ne sont pas des gestes distincts. Le registre de preuves est la pièce
# qu'on oppose : il doit refléter des consultations, pas des allers-retours du
# navigateur.
RENEW_VIEW_EVIDENCE_DEDUP_MINUTES = 15


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

    # -------------------------------------------------------------------------
    # Renouvellement — outillage partagé par la variante authentifiée et la
    # variante publique. Les deux passent par le MÊME écran de confirmation et
    # la MÊME séquence copie → octroi → preuve, sinon deux clients qui font le
    # même geste n'ont ni le même vécu ni le même dossier de preuve.
    # -------------------------------------------------------------------------

    # Ce que chaque état veut dire, en clair, dans une note de preuve. La page
    # et le dossier de preuve doivent raconter la MÊME chose : c'est ici que le
    # vocabulaire est fixé une seule fois.
    _NOTICE_STATE_NOTES = {
        "current": "version en vigueur au moment du geste",
        "pending": (
            "texte courant du modèle, aucune version publiée : la version est "
            "figée au moment de la confirmation"
        ),
        "pinned": (
            "REPLI — aucune version courante n'a pu être résolue : texte déjà "
            "rattaché au consentement, présenté comme tel à la personne"
        ),
        "summary": (
            "REPLI — aucune version d'avis : résumé en langage clair de la "
            "finalité, présenté comme tel à la personne"
        ),
    }

    def _renewal_notice_context(self, consent, allow_write=False):
        """Le TEXTE à faire lire, et la version à épingler ensuite.

        Retourne un dict : ``version`` (recordset, possiblement vide), ``body``
        (le HTML réellement affichable), ``state`` et ``error``.

        ⚠ La garde porte sur le TEXTE, pas sur la version. Un consentement sans
        modèle NI version épinglée rendait auparavant « pas de version, pas
        d'erreur » : l'écran s'affichait sans le moindre texte d'avis, avec la
        case « J'ai lu l'avis ci-dessus » et le bouton d'octroi, et la preuve
        ``renew`` scellée ensuite affirmait « après confirmation de lecture de
        l'avis » alors que rien n'avait été montré. ``notice_id`` et
        ``notice_version_id`` sont tous deux optionnels sur le modèle : le cas
        n'a rien de théorique.

        ⚠ ``allow_write`` : seul le POST a le droit de FIGER une version.
        ``_ensure_current_version()`` CRÉE une v1.0 quand il n'y en a pas — un
        artefact immuable (l'écriture refuse ``body``/``hash`` dès qu'un
        consentement l'utilise), dont le corps est choisi d'après la langue de
        l'utilisateur courant. Le faire naître sur un simple GET anonyme figeait
        le texte juridique d'après la langue de l'utilisateur public : bon par
        chance sur ce locataire (fr_CA partout), rien ne le garantit ailleurs
        dans le SaaS. Au GET on lit donc ``current_version_id`` (un calcul, sans
        écriture) puis, à défaut, le corps du modèle — c'est-à-dire exactement
        le texte que le POST figera en v1.0.

        ⚠ ``notice_version_id`` est ``copy=True`` : sans imposer explicitement
        la version courante au POST, ``consent.copy()`` recopie la version figée
        sur l'ANCIEN consentement, et le garde-fou de
        ``privacy.consent.create()`` ne se déclenche pas puisque la copie
        fournit déjà une valeur. Si l'organisation a publié une v2 (finalité
        élargie, nouveau destinataire, nouvelle durée), la personne
        reconsentirait à un texte périmé.
        """
        Version = request.env["privacy.notice.version"]
        notice = consent.notice_id
        pinned = consent.notice_version_id
        version = Version
        body = False
        state = False

        if notice:
            if allow_write:
                try:
                    # ⚠ Savepoint obligatoire : _ensure_current_version() ÉCRIT.
                    # Sans lui, une erreur SQL (p. ex. la contrainte d'unicité
                    # « une version par numéro et par modèle ») laisserait le
                    # curseur inutilisable, et le repli ci-dessous échouerait à
                    # son tour au lieu de sauver le geste du client.
                    with request.env.cr.savepoint():
                        version = notice.sudo()._ensure_current_version()
                except UserError:
                    # Le modèle n'a ni corps français ni corps anglais.
                    _logger.warning(
                        "Renouvellement du consentement %s : le modèle « %s » "
                        "(id %s) n'a aucun contenu, impossible de résoudre une "
                        "version courante.",
                        consent.id, notice.name, notice.id,
                    )
                    version = Version
                except Exception:  # noqa: BLE001 - une route publique ne remonte pas de trace
                    _logger.exception(
                        "Renouvellement du consentement %s : échec de résolution "
                        "de la version courante du modèle %s.",
                        consent.id, notice.id,
                    )
                    version = Version
            else:
                version = notice.sudo().current_version_id

            if version and version.body:
                state = "current"
                body = version.body
            elif not allow_write:
                # GET en lecture seule : montrer le texte que la confirmation
                # figera en v1.0, sans le créer maintenant. Le POST empruntera
                # le même chemin de langue, donc le texte lu et le texte scellé
                # sont bien le même.
                candidate = notice.sudo()._body_for_current_lang()
                if candidate:
                    version = Version
                    state = "pending"
                    body = candidate

        if not body and pinned and pinned.body:
            # Repli : rien de plus récent n'est résoluble, mais le texte déjà
            # rattaché au consentement reste affichable. Mieux vaut renouveler
            # dessus que refuser le geste — à la condition de le DIRE, à l'écran
            # comme dans la preuve.
            version = pinned
            state = "pinned"
            body = pinned.body

        if not body:
            summary = consent.purpose_id.plain_language_summary
            if summary:
                version = Version
                state = "summary"
                body = summary

        if not body:
            # Plus aucun texte à faire lire : demander « j'ai lu l'avis
            # ci-dessus » devant un cadre vide n'aurait aucune valeur, et la
            # preuve qui en découlerait serait fausse sur le point précis que
            # cet écran devait établir.
            _logger.warning(
                "Renouvellement du consentement %s : aucun texte affichable "
                "(modèle %s, version épinglée %s, finalité %s).",
                consent.id, notice.id or "aucun", pinned.id or "aucune",
                consent.purpose_id.id or "aucune",
            )
            return {
                "version": Version,
                "body": False,
                "state": "none",
                "error": "notice_unavailable",
            }

        return {"version": version, "body": body, "state": state, "error": None}

    def _notice_version_label(self, version):
        """Libellé traçable d'une version d'avis, pour les notes de preuve."""
        if not version:
            return "aucune version d'avis rattachée"
        return (
            f"avis {version.display_name} (id {version.id}, "
            f"entrée en vigueur le {version.effective_date or 'n/d'}, "
            f"empreinte {version.hash or 'absente'})"
        )

    def _notice_evidence_label(self, notice_ctx):
        """Libellé de preuve qui n'affirme QUE ce qui a été montré à l'écran.

        La note du dossier de preuve doit dire d'où vient le texte lu : sans
        cet état, une preuve ``renew`` scellée sur un texte de repli se lisait
        comme une preuve scellée sur l'avis en vigueur.
        """
        state_note = self._NOTICE_STATE_NOTES.get(notice_ctx.get("state"))
        label = self._notice_version_label(notice_ctx.get("version"))
        return f"{label} — {state_note}" if state_note else label

    def _log_renew_view_evidence(self, consent, notice_ctx, access_type):
        """Preuve « view » de l'écran de confirmation, dédoublonnée.

        C'est elle qui établira plus tard que le texte a bel et bien été
        AFFICHÉ à la personne avant son geste.

        ⚠ Une preuve par affichage remplissait le registre d'entrées qui ne
        correspondent à aucun geste : rechargement, retour arrière,
        préchargement du navigateur, et surtout POST refusé faute de case
        cochée, qui redirige vers ce même GET. On ne consigne donc qu'une
        consultation par fenêtre courte, pour un même consentement, un même
        canal et une même session (l'adresse IP sert de repli quand la session
        n'a pas d'identifiant).

        Un échec de journalisation ne doit pas empêcher l'affichage : il est
        capté ici.
        """
        try:
            Evidence = request.env["privacy.consent.evidence"].sudo()
            since = fields.Datetime.now() - timedelta(
                minutes=RENEW_VIEW_EVIDENCE_DEDUP_MINUTES
            )
            domain = [
                ("consent_id", "=", consent.id),
                ("consent_action", "=", "view"),
                ("access_type", "=", access_type),
                ("collected_at", ">=", fields.Datetime.to_string(since)),
            ]
            session_id = getattr(request.session, "sid", None)
            if session_id:
                domain.append(("session_id", "=", session_id))
            else:
                domain.append(
                    ("ip_address", "=", request.httprequest.remote_addr)
                )
            if Evidence.search_count(domain):
                return

            Evidence.create_from_http_request(
                consent=consent,
                action="view",
                note=(
                    "Écran de confirmation de renouvellement affiché — "
                    f"{self._notice_evidence_label(notice_ctx)}"
                ),
                request_obj=request,
                access_type=access_type,
            )
        except Exception:  # noqa: BLE001 - l'affichage prime sur la journalisation
            _logger.exception(
                "Consentement %s : échec de la preuve d'affichage de l'avis de "
                "renouvellement.",
                consent.id,
            )

    def _render_renew_confirm(self, consent, notice_ctx, renew_url, is_public, access_token, partner):
        """Rendre l'écran de confirmation du renouvellement."""
        access_type = "portal_public" if is_public else "portal_authenticated"
        self._log_renew_view_evidence(consent, notice_ctx, access_type)

        current_version = notice_ctx["version"]
        previous_version = consent.notice_version_id
        values = {
            "page_name": "privacy_consent_renew_confirm",
            "consent": consent,
            "partner": partner,
            "is_public": is_public,
            "access_token": access_token,
            "renew_url": renew_url,
            "current_version": current_version,
            "previous_version": previous_version,
            # L'encadré « l'avis a changé » ne se justifie que si la personne
            # avait effectivement consenti à une AUTRE version auparavant.
            "notice_changed": bool(
                current_version and previous_version
                and current_version.id != previous_version.id
            ),
            "notice_html": notice_ctx["body"],
            "notice_state": notice_ctx["state"],
            # ⚠ Le gabarit doit savoir qu'il montre un texte de REPLI : sans
            # cela, la page annonçait « l'avis en vigueur aujourd'hui » devant
            # l'ancienne version épinglée, et affirmait au client quelque chose
            # que le code sait faux.
            "notice_is_fallback": notice_ctx["state"] in ("pinned", "summary"),
        }
        return request.render("privacy_consent.portal_consent_renew_confirm", values)

    def _perform_renewal(self, consent, notice_ctx, access_type, actor):
        """Dupliquer le consentement, l'accorder, puis sceller la preuve.

        ``actor`` décrit le canal et la personne (« via le portail par X »,
        « via le lien public ») ; il alimente les notes, le chatter et la preuve.

        ⚠ L'ordre compte. La preuve est créée APRÈS ``action_grant()`` : créée
        avant, l'instantané signé portait « status: draft, granted_at: None »
        et le certificat PDF qu'on oppose disait littéralement « brouillon,
        accordé le : néant ».

        Les exceptions remontent volontairement à l'appelant, qui annule la
        transaction : un échec partiel ne doit pas rester en base pendant qu'on
        annonce au client que le renouvellement a échoué.
        """
        previous_status = consent.status
        current_version = notice_ctx["version"]
        vals = {
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
            # ⚠ Les identifiants LibreSign se recopient eux aussi : sans ces
            # remises à zéro, le renouvellement hérite de la référence de
            # signature du consentement PRÉCÉDENT. Le chemin dorsal
            # (``action_renew()``) les efface déjà ; le portail ne le faisait pas.
            "libresign_file_uuid": False,
            "libresign_status": False,
            "libresign_sent_at": False,
            "libresign_completed_at": False,
            "last_reminder_sent_at": False,
            "reminder_count": 0,
            "renewed_from_id": consent.id,
            "notes": (
                f"Reconsentement {actor} le {fields.Date.today()} "
                f"(statut précédent : {previous_status})"
            ),
        }
        if current_version:
            # ⚠ Imposer explicitement la version COURANTE : c'est le seul
            # moyen de neutraliser le copy=True de notice_version_id.
            vals["notice_version_id"] = current_version.id
            if current_version.notice_id:
                vals["notice_id"] = current_version.notice_id.id

        new_consent = consent.copy(vals)

        # Lier l'ancien consentement au nouveau
        consent.renewed_to_id = new_consent.id

        # Accorder le nouveau consentement, PUIS sceller la preuve
        new_consent.action_grant()

        # ⚠ La note cite l'ÉTAT de la résolution, pas seulement la version : sur
        # un repli, la preuve ne doit pas se lire comme si l'avis en vigueur
        # avait été montré. Elle ne peut pas affirmer plus que ce qui a été
        # affiché à l'écran.
        version_label = self._notice_evidence_label(notice_ctx)
        request.env["privacy.consent.evidence"].sudo().create_from_http_request(
            consent=new_consent,
            action="renew",
            note=(
                f"Reconsentement depuis le consentement #{consent.id} "
                f"(était {previous_status}) {actor}, après confirmation de "
                f"lecture du texte affiché à l'écran. Texte affiché et "
                f"retenu : {version_label}"
            ),
            request_obj=request,
            access_type=access_type,
        )

        consent.message_post(
            body=(
                f"Consentement renouvelé {actor}. Nouveau consentement : "
                f"#{new_consent.id}. Texte affiché et retenu : {version_label}"
            ),
            message_type="notification",
        )
        return new_consent

    def _send_renewal_confirmation(self, new_consent):
        """Courriel de confirmation de renouvellement.

        ⚠ Dans son propre try/except, et appelé APRÈS le renouvellement : un
        échec SMTP ne doit pas invalider un octroi déjà acquis. La personne a
        cliqué, sa réponse est faite.
        """
        try:
            template = request.env.ref(
                "privacy_consent.mail_template_consent_renewal_confirmation",
                raise_if_not_found=False,
            )
            if template:
                template.sudo().send_mail(new_consent.id, force_send=True)
        except Exception:  # noqa: BLE001 - l'octroi prime sur la confirmation
            _logger.exception(
                "Renouvellement %s : échec de l'envoi du courriel de confirmation.",
                new_consent.id,
            )

    @http.route(
        "/my/privacy/consent/<int:consent_id>/renew",
        type="http",
        auth="user",
        website=True,
        methods=["GET", "POST"],
    )
    def portal_consent_renew(self, consent_id, **kw):
        """Renouvellement depuis le portail authentifié : confirmation puis octroi.

        Permet de renouveler les consentements accordés, expirés ou retirés.

        ⚠ Un consentement REFUSÉ n'est PAS renouvelable ici — décision 50 du CR
        PMEC #139 (2026-08-03). Le lien d'origine est consommé par le refus ;
        seule une nouvelle demande émise par l'organisation rouvre la porte.

        ⚠ GET et POST partagent le MÊME gestionnaire et la MÊME URL : le
        ``<form>``/lien du gabarit de détail sert les deux variantes (publique
        et authentifiée) depuis la même ligne, et scinder en deux routes aurait
        invalidé l'URL déjà en circulation.
        """
        partner = request.env.user.partner_id
        consent = request.env["privacy.consent"].sudo().browse(consent_id)

        if not self._can_access_consent(partner, consent):
            return request.redirect("/my/privacy/consents")

        # ⚠ Le refus a son propre message : « ce consentement ne peut pas être
        # renouvelé » laisserait croire à une panne passagère, alors que c'est
        # une fin de parcours qui demande un geste de l'organisation.
        if consent.status == "refused":
            return request.redirect(
                f"/my/privacy/consent/{consent_id}?error=refused_link_closed"
            )

        # Peut renouveler les consentements accordés, expirés ou retirés
        if consent.status not in PORTAL_RENEWABLE_STATES:
            return request.redirect(f"/my/privacy/consent/{consent_id}?error=cannot_renew")

        # Vérifier si déjà renouvelé
        if consent.renewed_to_id:
            return request.redirect(f"/my/privacy/consent/{consent.renewed_to_id.id}")

        renew_url = f"/my/privacy/consent/{consent_id}/renew"
        is_post = request.httprequest.method == "POST"

        # Étape 1 (GET) : montrer le texte COURANT, en lecture seule, et
        # demander une confirmation.
        if not is_post:
            notice_ctx = self._renewal_notice_context(consent, allow_write=False)
            if notice_ctx["error"]:
                return request.redirect(
                    f"/my/privacy/consent/{consent_id}?error={notice_ctx['error']}"
                )
            return self._render_renew_confirm(
                consent, notice_ctx, renew_url,
                is_public=False, access_token=None, partner=partner,
            )

        # Étape 2 (POST) : sans confirmation de lecture, pas de reconsentement.
        # ⚠ Ce contrôle vient AVANT la résolution en écriture : un POST forgé
        # sans la case ne doit rien faire naître en base.
        if not kw.get("notice_read"):
            return request.redirect(f"{renew_url}?error=notice_not_read")

        # C'est ici, et seulement ici, que la version définitive est épinglée.
        notice_ctx = self._renewal_notice_context(consent, allow_write=True)
        if notice_ctx["error"]:
            return request.redirect(
                f"/my/privacy/consent/{consent_id}?error={notice_ctx['error']}"
            )

        try:
            new_consent = self._perform_renewal(
                consent, notice_ctx, "portal_authenticated",
                f"via le portail par {partner.name}",
            )
        except Exception:  # noqa: BLE001
            # ⚠ Annuler explicitement : sans ce rollback, un échec partiel
            # restait en base pendant qu'on annonçait l'échec au client.
            request.env.cr.rollback()
            _logger.exception(
                "Échec du renouvellement du consentement %s via le portail "
                "authentifié.",
                consent_id,
            )
            return request.redirect(f"/my/privacy/consent/{consent_id}?error=renewal_failed")

        self._send_renewal_confirmation(new_consent)

        return request.redirect(f"/my/privacy/consent/{new_consent.id}?renewed=1")

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
        methods=["GET", "POST"],
    )
    def public_consent_renew(self, consent_id, access_token, **kw):
        """Renouvellement depuis l'URL publique : confirmation puis octroi.

        ⚠ Un consentement REFUSÉ n'est PAS renouvelable ici — décision 50 du CR
        PMEC #139 (2026-08-03). C'est LE point de la décision : le lien reçu par
        courriel restait « chaud » indéfiniment, et un refus se retournait en
        octroi d'un clic depuis la boîte de réception, sur le même lien. Il faut
        désormais une nouvelle demande, avec son propre jeton, émise par
        l'organisation.

        ⚠ Le jeton reste valide pour la LECTURE : la personne doit pouvoir
        rouvrir son courriel et constater ce qu'elle a refusé, et quand. C'est
        le pouvoir d'ACCORDER qui s'éteint, pas la page.

        ⚠ Même gestionnaire pour le GET (écran de confirmation) et le POST
        (renouvellement effectif) : l'URL en circulation reste valide et le
        gabarit de détail continue de servir les deux variantes depuis la même
        ligne.
        """
        consent = request.env["privacy.consent"].sudo().browse(consent_id)

        # Valider le jeton
        if not consent.exists() or consent.access_token != access_token:
            return request.render("privacy_consent.portal_consent_invalid_token", {
                "page_name": "privacy_consent_error",
            })

        if consent.status == "refused":
            return request.redirect(
                f"/privacy/consent/{consent_id}/{access_token}"
                "?error=refused_link_closed"
            )

        # Peut renouveler les consentements accordés, expirés ou retirés
        if consent.status not in PORTAL_RENEWABLE_STATES:
            return request.redirect(f"/privacy/consent/{consent_id}/{access_token}?error=cannot_renew")

        # Vérifier si déjà renouvelé
        if consent.renewed_to_id:
            new_token = consent.renewed_to_id.access_token
            return request.redirect(f"/privacy/consent/{consent.renewed_to_id.id}/{new_token}")

        renew_url = f"/privacy/consent/{consent_id}/{access_token}/renew"
        is_post = request.httprequest.method == "POST"
        detail_url = f"/privacy/consent/{consent_id}/{access_token}"

        # Étape 1 (GET) : montrer le texte COURANT, en lecture seule, et
        # demander une confirmation. ⚠ Ce GET est anonyme : il ne doit RIEN
        # créer, ni version d'avis ni preuve en double.
        if not is_post:
            notice_ctx = self._renewal_notice_context(consent, allow_write=False)
            if notice_ctx["error"]:
                return request.redirect(f"{detail_url}?error={notice_ctx['error']}")
            return self._render_renew_confirm(
                consent, notice_ctx, renew_url,
                is_public=True, access_token=access_token,
                partner=consent.subject_partner_id,
            )

        # Étape 2 (POST) : sans confirmation de lecture, pas de reconsentement.
        # ⚠ Ce contrôle vient AVANT la résolution en écriture : un POST forgé
        # sans la case ne doit rien faire naître en base.
        if not kw.get("notice_read"):
            return request.redirect(f"{renew_url}?error=notice_not_read")

        # C'est ici, et seulement ici, que la version définitive est épinglée.
        notice_ctx = self._renewal_notice_context(consent, allow_write=True)
        if notice_ctx["error"]:
            return request.redirect(f"{detail_url}?error={notice_ctx['error']}")

        try:
            new_consent = self._perform_renewal(
                consent, notice_ctx, "portal_public", "via le lien public",
            )
        except Exception:  # noqa: BLE001
            # ⚠ Annuler explicitement : sans ce rollback, un échec partiel
            # restait en base pendant qu'on annonçait l'échec au client.
            request.env.cr.rollback()
            _logger.exception(
                "Échec du renouvellement du consentement %s via le lien public.",
                consent_id,
            )
            return request.redirect(
                f"/privacy/consent/{consent_id}/{access_token}?error=renewal_failed"
            )

        # Même confirmation que la variante authentifiée : deux clients qui
        # font le même geste doivent recevoir la même chose.
        self._send_renewal_confirmation(new_consent)

        # Rediriger vers le nouveau consentement avec son jeton
        return request.redirect(
            f"/privacy/consent/{new_consent.id}/{new_consent.access_token}?renewed=1"
        )
