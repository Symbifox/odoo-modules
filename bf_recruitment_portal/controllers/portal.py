# Part of bf_recruitment_portal. Voir LICENSE.
"""Les routes du portail candidat.

⚠️ **Aucun droit ORM n'est accordé au groupe portail.** Les recherches se font
en `sudo` avec un domaine borné au partenaire connecté, et l'accès unitaire
passe par `_document_check_access`, qui accepte soit un jeton signé, soit un
utilisateur qui a réellement le droit de lire. C'est le patron de
`bf_meeting_portal`.

⚠️ **Les gabarits ne reçoivent JAMAIS l'enregistrement**, seulement les
dictionnaires construits par les listes blanches du modèle. C'est ce qui rend
les notes individuelles et les noms d'évaluateurs inatteignables depuis une
page, même si un gabarit est modifié plus tard par distraction.
"""

import hmac
import threading
import time
from collections import defaultdict

from odoo import fields, http
from odoo.exceptions import AccessError, MissingError
from odoo.http import request

from odoo.addons.portal.controllers.portal import CustomerPortal


class _Limiteur:
    """Compteur d'évènements à fenêtre glissante, par travailleur.

    ⚠️ Recopié de `bf_securetransfer` plutôt qu'importé : un module de
    recrutement n'a pas à dépendre d'un module de transfert de fichiers pour
    fonctionner. La contrepartie d'une copie est qu'elle vieillit à part, et
    c'est assumé pour vingt lignes sans état partagé.

    ⚠️ Par travailleur, donc approximatif sur plusieurs processus. Ça suffit
    pour contenir une rafale, ce qui est tout ce qu'on lui demande.
    """

    def __init__(self, fenetre_secondes):
        self._verrou = threading.Lock()
        self._data = defaultdict(list)
        self._fenetre = fenetre_secondes

    def libre(self, cle, maximum):
        maintenant = time.monotonic()
        with self._verrou:
            seuil = maintenant - self._fenetre
            evenements = [t for t in self._data[cle] if t > seuil]
            self._data[cle] = evenements
            return len(evenements) < maximum

    def compter(self, cle):
        with self._verrou:
            self._data[cle].append(time.monotonic())

    def consommer(self, cle, maximum):
        """Contrôle et enregistrement en un seul geste."""
        maintenant = time.monotonic()
        with self._verrou:
            seuil = maintenant - self._fenetre
            evenements = [t for t in self._data[cle] if t > seuil]
            if len(evenements) >= maximum:
                self._data[cle] = evenements
                return False
            evenements.append(maintenant)
            self._data[cle] = evenements
            return True


# Les codes ratés, par (IP, candidature).
_echecs = _Limiteur(900)
_ECHECS_MAX = 8
# 🔴 Et les ENVOIS RÉUSSIS, ce qui n'est pas la même chose : sans ce second
# compteur, qui détient le lien peut boucler sur « renvoyer un code » et
# inonder la boîte de la personne. Même leçon que `bf_securetransfer`.
_envois = _Limiteur(900)
_ENVOIS_MAX = 10
_repos = _Limiteur(30)


class PortalCandidature(CustomerPortal):

    # ------------------------------------------------------------------
    # Le lien qui ne mène plus à rien
    # ------------------------------------------------------------------

    def _candidature_lien_perime(self):
        """La page servie quand l'accès échoue, quelle qu'en soit la raison.

        🔴 **La MÊME réponse pour un dossier détruit et pour un jeton faux.**
        Les distinguer dirait à qui essaie des numéros lesquels ont existé.
        Avant, les deux redirigeaient vers `/my`, ce qui n'était pas un oracle
        non plus, mais menait un visiteur sans compte à une page de CONNEXION :
        le lien de la lettre de refus se lisait donc comme un compte cassé
        plutôt que comme une conservation arrivée à son terme.

        ⚠️ 404, parce que la ressource n'est effectivement plus là. La page
        rend quand même, et c'est elle qui compte pour la personne.
        """
        reponse = request.render("bf_recruitment_portal.portal_lien_perime", {
            "page_name": "candidature",
        })
        reponse.status_code = 404
        return reponse

    # ------------------------------------------------------------------
    # Accueil du portail
    # ------------------------------------------------------------------

    def _candidature_domain(self):
        """Les candidatures du partenaire connecté, et rien d'autre."""
        partner = request.env.user.partner_id
        if not partner:
            return [("id", "=", 0)]
        return [("partner_id", "=", partner.id)]

    def _prepare_home_portal_values(self, counters):
        values = super()._prepare_home_portal_values(counters)
        if "candidature_count" in counters:
            values["candidature_count"] = request.env["hr.applicant"].sudo().search_count(
                self._candidature_domain())
        return values

    # ------------------------------------------------------------------
    # Mes candidatures
    # ------------------------------------------------------------------

    @http.route(["/my/candidatures"], type="http", auth="user", website=True)
    def portal_mes_candidatures(self, **kw):
        candidatures = request.env["hr.applicant"].sudo().search(
            self._candidature_domain(), order="create_date desc")
        return request.render("bf_recruitment_portal.portal_liste", {
            "page_name": "candidature",
            "candidatures": [c._portal_summary() for c in candidatures],
        })

    # ------------------------------------------------------------------
    # Une candidature
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # La barrière du code à usage unique
    # ------------------------------------------------------------------

    @staticmethod
    def _cle_session(applicant, quoi):
        return "rec_otp_%s_%s" % (quoi, applicant.id)

    def _otp_franchi(self, applicant):
        """Le code a-t-il été validé dans CETTE session ?"""
        return bool(request.session.get(self._cle_session(applicant, "ok")))

    def _otp_barriere(self, applicant, access_token, erreur=None):
        """Rend la page du code, ou None quand il n'y a rien à demander.

        🔴 **Elle RETOURNE une page, elle ne redirige pas vers la page qu'elle
        garde.** Une barrière qui renvoie vers l'URL d'où elle est appelée
        boucle à l'infini : le secret tient, la personne se heurte à
        `ERR_TOO_MANY_REDIRECTS` sans la moindre explication, et l'exploitant
        ne voit qu'un visiteur qui « n'arrive pas à ouvrir le lien ». Leçon
        payée par `bf_securetransfer_sign` le 2026-08-21, pas à repayer.
        """
        if not applicant._portal_otp_required() or self._otp_franchi(applicant):
            return None
        envoye = bool(request.session.get(self._cle_session(applicant, "chal")))
        return request.render("bf_recruitment_portal.portal_code", {
            "page_name": "candidature",
            "applicant_id": applicant.id,
            "access_token": access_token,
            "adresse_indice": applicant.sudo()._portal_otp_indice(),
            "deja_envoye": envoye,
            "erreur": erreur,
        })

    @http.route(["/my/candidature/<int:applicant_id>/code"], type="http",
                auth="public", methods=["POST"], csrf=False, website=True)
    def portal_code_demande(self, applicant_id, access_token=None, **kw):
        """Envoyer un code à l'adresse DU DOSSIER.

        ⚠️ `csrf=False` est sans danger ici : la route n'est atteignable qu'avec
        le jeton du lien, l'envoi est plafonné par (IP, candidature), et une
        réussite ne fait qu'écrire un défi dans la session de ce visiteur.
        """
        try:
            applicant = self._document_check_access(
                "hr.applicant", applicant_id, access_token)
        except (AccessError, MissingError):
            return self._candidature_lien_perime()
        if not applicant._portal_otp_required():
            return request.redirect(self._url_dossier(applicant, access_token))

        cle = "%s:%s" % (request.httprequest.remote_addr or "?", applicant.id)
        if not _repos.consommer(cle, 1):
            return self._otp_barriere(applicant, access_token, erreur="repos")
        if not _envois.consommer(cle, _ENVOIS_MAX):
            return self._otp_barriere(applicant, access_token, erreur="trop")

        empreinte, expiration = applicant.sudo()._portal_otp_send()
        if not empreinte:
            return self._otp_barriere(applicant, access_token, erreur="sans_adresse")
        request.session[self._cle_session(applicant, "chal")] = {
            "hash": empreinte,
            "expiry": fields.Datetime.to_string(expiration),
        }
        return self._otp_barriere(applicant, access_token, erreur=None)

    @http.route(["/my/candidature/<int:applicant_id>/verifier"], type="http",
                auth="public", methods=["POST"], csrf=False, website=True)
    def portal_code_verifie(self, applicant_id, access_token=None, code=None, **kw):
        try:
            applicant = self._document_check_access(
                "hr.applicant", applicant_id, access_token)
        except (AccessError, MissingError):
            return self._candidature_lien_perime()
        if not applicant._portal_otp_required():
            return request.redirect(self._url_dossier(applicant, access_token))

        cle = "%s:%s" % (request.httprequest.remote_addr or "?", applicant.id)
        if not _echecs.libre(cle, _ECHECS_MAX):
            return self._otp_barriere(applicant, access_token, erreur="bloque")

        defi = request.session.get(self._cle_session(applicant, "chal")) or {}
        expiration = defi.get("expiry")
        perime = (not expiration) or fields.Datetime.from_string(
            expiration) < fields.Datetime.now()
        Modele = request.env["hr.applicant"].sudo()
        # ⚠️ `compare_digest` et non `==` : une comparaison qui s'arrête au
        # premier caractère différent se chronomètre.
        if defi.get("hash") and not perime and hmac.compare_digest(
                defi["hash"], Modele._portal_otp_hash((code or "").strip())):
            request.session[self._cle_session(applicant, "ok")] = True
            request.session.pop(self._cle_session(applicant, "chal"), None)
            return request.redirect(self._url_dossier(applicant, access_token), code=303)
        _echecs.compter(cle)
        return self._otp_barriere(
            applicant, access_token, erreur="perime" if perime else "faux")

    @staticmethod
    def _url_dossier(applicant, access_token):
        suffixe = ("?access_token=%s" % access_token) if access_token else ""
        return "/my/candidature/%s%s" % (applicant.id, suffixe)

    @http.route(["/my/candidature/<int:applicant_id>"], type="http",
                auth="public", website=True)
    def portal_candidature(self, applicant_id, access_token=None, **kw):
        try:
            applicant = self._document_check_access(
                "hr.applicant", applicant_id, access_token)
        except (AccessError, MissingError):
            return self._candidature_lien_perime()
        barriere = self._otp_barriere(applicant, access_token)
        if barriere is not None:
            return barriere
        return request.render("bf_recruitment_portal.portal_detail", {
            "page_name": "candidature",
            "dossier": applicant._portal_summary(),
            "seances": applicant._portal_interviews(),
            "signup_url": applicant._portal_signup_url() if request.env.user._is_public() else False,
            "access_token": access_token,
            "reset_url": "/web/reset_password",
        })

    # ------------------------------------------------------------------
    # Le cahier
    # ------------------------------------------------------------------

    @http.route(["/my/candidature/<int:applicant_id>/cahier"], type="http",
                auth="public", website=True)
    def portal_cahier(self, applicant_id, access_token=None, **kw):
        """Le cahier d'entrevues, version remise à la personne évaluée.

        🔴 Deux gardes, et les deux comptent. Le premier est l'accès :
        `_document_check_access` refuse un jeton qui ne correspond pas. Le
        second est la DÉCISION : tant qu'elle n'est pas rendue, il n'y a rien à
        remettre, et servir le cahier ici contournerait le garde du modèle.

        ⚠️ Le rapport servi est `report_interview_book_candidate`, jamais
        `report_interview_book`. Le premier retire le nom des évaluateurs, le
        second les nomme tous.
        """
        try:
            applicant = self._document_check_access(
                "hr.applicant", applicant_id, access_token)
        except (AccessError, MissingError):
            return self._candidature_lien_perime()
        barriere = self._otp_barriere(applicant, access_token)
        if barriere is not None:
            return barriere
        # 🔴 L'interrupteur du locataire se contrôle ICI aussi, pas seulement
        # dans le gabarit : cacher un bouton n'est pas un contrôle d'accès.
        if not applicant._portal_book_enabled():
            return request.redirect(self._url_dossier(applicant, access_token), code=303)
        if not applicant._portal_decision_taken():
            return request.redirect(applicant.access_url)

        pdf, _dummy = request.env["ir.actions.report"].sudo()._render_qweb_pdf(
            "bf_recruitment.report_interview_book_candidate", [applicant.id])
        nom = "cahier-entrevues-%s.pdf" % applicant.id
        return request.make_response(pdf, headers=[
            ("Content-Type", "application/pdf"),
            ("Content-Length", len(pdf)),
            ("Content-Disposition", "attachment; filename=%s" % nom),
        ])
