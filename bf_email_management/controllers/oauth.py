"""Le retour du fournisseur après consentement.

Trois gardes, et aucune n'est décorative :

1. ``auth="user"`` : le point d'entrée n'existe pas pour un anonyme.
2. Le compte est lu dans l'environnement de la personne connectée, donc la
   règle d'enregistrement du propriétaire s'applique : on ne peut pas poser
   un jeton sur la fiche de quelqu'un d'autre, même en connaissant son id.
3. L'état est signé (HMAC de l'instance). Sans lui, un `code` fabriqué
   ailleurs pourrait être présenté ici.

⚠️ La rédaction du message d'erreur compte : un consentement refusé est le
cas NORMAL (la personne a cliqué « Annuler », ou son administrateur bloque le
consentement usager, ce qui est courant en entreprise). Il ne doit pas
ressembler à une panne.
"""

import json
import logging

from odoo import _, http
from odoo.http import request

from ..models.bf_email_autoconfig import FOURNISSEURS

_logger = logging.getLogger(__name__)


class BfEmailOauthController(http.Controller):

    @http.route("/bf_email/oauth/retour", type="http", auth="user",
                methods=["GET"], csrf=False, save_session=False)
    def retour(self, **parametres):
        etat_brut = parametres.get("state") or ""
        try:
            compte_id = int(json.loads(etat_brut).get("compte") or 0)
        except (ValueError, TypeError, AttributeError):
            compte_id = 0
        if not compte_id:
            return self._page(_("Lien incomplet"), _(
                "Ce retour ne porte pas de demande reconnaissable. "
                "Recommencez depuis l'assistant."))

        Compte = request.env["bf.email.account"]
        compte = Compte.browse(compte_id).exists()
        # La lecture passe par la règle d'enregistrement : un compte qui n'est
        # pas le sien se comporte comme un compte qui n'existe pas.
        if not compte or not compte.has_access("write"):
            return self._page(_("Compte introuvable"), _(
                "Ce compte n'existe pas, ou il ne vous appartient pas."))

        if not request.env["bf.email.oauth"]._verifier_etat(etat_brut, compte):
            return self._page(_("Demande non reconnue"), _(
                "La signature de la demande ne correspond pas. Par sécurité, "
                "rien n'a été enregistré. Relancez « Lier le compte »."))

        if parametres.get("error"):
            detail = parametres.get("error_description") or parametres["error"]
            return self._page(_("Consentement non accordé"), _(
                "Le fournisseur n'a pas accordé l'accès : %(d)s\n\n"
                "Si votre organisation bloque l'autorisation des "
                "applications tierces, c'est votre équipe TI qui doit "
                "l'accorder une fois pour toutes.", d=detail))

        code = parametres.get("code")
        if not code:
            return self._page(_("Retour vide"), _("Le fournisseur n'a rien renvoyé."))

        try:
            jetons = request.env["bf.email.oauth"].echanger_le_code(
                compte.oauth_provider, code)
        except Exception as exc:
            _logger.warning("OAuth %s : échange refusé (%s)", compte.id, exc)
            return self._page(_("Échange refusé"), str(exc))

        if not jetons.get("refresh_token"):
            # Sans jeton de rafraîchissement, la connexion mourrait à la
            # première expiration, une heure plus tard, sans rien dire.
            return self._page(_("Autorisation incomplète"), _(
                "Le fournisseur n'a pas donné de jeton de rafraîchissement. "
                "La connexion aurait expiré en une heure. Recommencez en "
                "acceptant l'accès hors ligne."))

        compte.sudo().write({
            "auth_mode": "xoauth2",
            "password": False,
            "oauth_refresh_token": jetons["refresh_token"],
            "oauth_access_token": jetons["access_token"],
            "oauth_expiration": jetons["expiration"],
        })

        # ⚠️ Le contrôle qui tranche est la session IMAP, pas le jeton reçu.
        # Un jeton valide dont la portée ne couvre pas IMAP se lit comme un
        # succès ici et échoue au premier cron.
        try:
            conn = compte._ouvrir_imap(timeout=20)
            conn.logout()
        except Exception as exc:
            compte.sudo().write({"state": "error", "last_error": str(exc)})
            return self._page(_("Jeton reçu, boîte inaccessible"), _(
                "Le consentement a été accordé, mais la boîte refuse encore "
                "la session : %(e)s\n\nC'est en général une portée manquante "
                "sur l'inscription d'application (IMAP.AccessAsUser.All chez "
                "Microsoft).", e=exc))

        compte.sudo().write({"state": "connected", "last_error": False})
        compte._semer_le_filigrane()
        request.env["bf.email.identity"]._sync_from_accounts(
            users=request.env.user)
        fiche = FOURNISSEURS.get(compte.oauth_provider or "") or {}
        if fiche.get("smtp"):
            hote, port, securite = fiche["smtp"]
            request.env["ir.mail_server"]._bf_assurer_pour_compte(
                compte, hote, port, securite)

        return request.redirect(
            "/odoo/action-bf_email_management.bf_email_account_action/%s" % compte.id)

    @staticmethod
    def _page(titre, message):
        return request.render("bf_email_management.oauth_retour", {
            "titre": titre, "message": message,
        })
