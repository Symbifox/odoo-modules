"""OAuth 2.0 par PERSONNE pour les comptes courriel.

Odoo sait déjà faire le ballet des jetons : `google_gmail` et
`microsoft_outlook` le font depuis des années. 🔴 Mais chez eux tout est
réservé à l'administrateur (`groups='base.group_system'` sur les champs,
`AccessError` explicite dans `open_google_gmail_uri`), parce que leur modèle
est « un serveur de courriel pour la société ». Le nôtre est « une boîte par
personne ». Le travail neuf n'est donc pas le protocole, c'est
de rendre ce geste personnel sans ouvrir de droits.

Pourquoi il le faut : mesuré le 2026-09-20, `outlook.office365.com` et
`imap-mail.outlook.com` répondent tous les deux `Basic authentication is
disabled.` Aucun mot de passe, ordinaire ou d'application, n'ouvre une boîte
Microsoft. 23 des 53 domaines de nos contacts sont là.

⚠️ Le secret client est un réglage d'instance (`ir.config_parameter`, lu en
sudo, jamais exposé à l'usager). Le jeton de rafraîchissement, lui, vit sur la
fiche du compte, sous la même règle d'enregistrement que le mot de passe IMAP
qu'il remplace : visible du seul propriétaire.
"""

import base64
import json
import logging
import time
from urllib.parse import urlencode

import requests

from odoo import _, api, models
from odoo.exceptions import UserError
from odoo.tools.misc import hmac
from hmac import compare_digest as hmac_compare

_logger = logging.getLogger(__name__)

DELAI_JETON = 10
# Durée de validité d'un état OAuth, en secondes.
VALIDITE_ETAT = 15 * 60
# Marge avant expiration : le temps de renouveler et d'ouvrir la session.
MARGE_EXPIRATION = 120

FOURNISSEURS_OAUTH = {
    "google": {
        "libelle": "Google",
        "autorisation": "https://accounts.google.com/o/oauth2/v2/auth",
        "jeton": "https://oauth2.googleapis.com/token",
        # ⚠️ Scope RESTREINT au sens de Google : au-delà de 100 usagers, il
        # déclenche une vérification annuelle et une évaluation de sécurité
        # CASA de niveau 2, facturée chaque année. Tant que ce n'est pas
        # tranché, le mot de passe d'application reste le chemin recommandé
        # pour Gmail, et il fonctionne (deux comptes en service depuis plusieurs semaines).
        "portee": "https://mail.google.com/",
        "extra": {"access_type": "offline", "prompt": "consent"},
        "icp": "bf_email.oauth_google",
    },
    "microsoft": {
        "libelle": "Microsoft",
        "autorisation": "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
        "jeton": "https://login.microsoftonline.com/common/oauth2/v2.0/token",
        # Les scopes exacts de la documentation Microsoft. `offline_access`
        # est ce qui rend le jeton de rafraîchissement.
        "portee": ("offline_access "
                   "https://outlook.office.com/IMAP.AccessAsUser.All "
                   "https://outlook.office.com/SMTP.Send"),
        "extra": {"response_mode": "query"},
        "icp": "bf_email.oauth_microsoft",
    },
}

CHEMIN_RETOUR = "/bf_email/oauth/retour"


class BfEmailOauth(models.AbstractModel):
    """🔴 Tout est `@api.private` ici, et ce n'est pas de la prudence.

    Un `AbstractModel` n'a pas de table : aucune règle d'enregistrement,
    aucune ligne d'`ir.model.access` ne se déclenche quand `call_kw` l'appelle.
    Une méthode sans tiret bas y est donc appelable par n'importe quel usager
    interne depuis la console de son navigateur. `rafraichir` et
    `echanger_le_code` font signer une demande de jeton par le secret client
    de l'instance ; `url_de_consentement` en fabrique l'URL. Rien de tout ça
    n'a de raison de sortir du Python de la maison.
    """

    _name = "bf.email.oauth"
    _description = "OAuth 2.0 des comptes courriel"

    # ------------------------------------------------------------------
    # Configuration d'instance
    # ------------------------------------------------------------------
    @api.model
    def _identifiants(self, fournisseur):
        """(client_id, client_secret) du fournisseur, ou (False, False)."""
        fiche = FOURNISSEURS_OAUTH.get(fournisseur)
        if not fiche:
            return False, False
        icp = self.env["ir.config_parameter"].sudo()
        return (icp.get_param(fiche["icp"] + "_client_id") or False,
                icp.get_param(fiche["icp"] + "_client_secret") or False)

    @api.model
    @api.private
    def est_configure(self, fournisseur):
        identifiant, secret = self._identifiants(fournisseur)
        return bool(identifiant and secret)

    @api.model
    def _uri_de_retour(self):
        base = self.env["ir.config_parameter"].sudo().get_param("web.base.url") or ""
        return base.rstrip("/") + CHEMIN_RETOUR

    # ------------------------------------------------------------------
    # Le jeton d'état, qui rattache le retour à une demande
    # ------------------------------------------------------------------
    @api.model
    def _etat(self, compte, horodatage=None):
        """Jeton d'état signé : compte, horodatage, et garde anti-CSRF.

        Sans lui, le point de retour accepterait un `code` fabriqué par un
        tiers et poserait ses jetons sur la fiche de quelqu'un d'autre.

        ⚠️ L'horodatage est DANS la signature, pas à côté : un état sans
        borne de temps reste valide pour toujours et peut être rejoué des mois
        plus tard, avec un code d'autorisation obtenu entre-temps. Quinze
        minutes suffisent largement à un écran de consentement.
        """
        quand = int(horodatage if horodatage is not None else time.time())
        empreinte = hmac(self.env(su=True), "bf_email_oauth",
                         f"{compte.id}-{compte.user_id.id}-{quand}")
        return json.dumps({"compte": compte.id, "t": quand, "jeton": empreinte})

    @api.model
    def _verifier_etat(self, etat_brut, compte):
        try:
            donnees = json.loads(etat_brut or "{}")
        except ValueError:
            return False
        if donnees.get("compte") != compte.id:
            return False
        quand = donnees.get("t")
        if not isinstance(quand, int):
            return False
        age = int(time.time()) - quand
        if age < -60 or age > VALIDITE_ETAT:
            return False
        attendu = hmac(self.env(su=True), "bf_email_oauth",
                       f"{compte.id}-{compte.user_id.id}-{quand}")
        # Comparaison à temps constant : le jeton est un secret dérivé.
        return hmac_compare(donnees.get("jeton") or "", attendu)

    # ------------------------------------------------------------------
    # Les trois gestes du protocole
    # ------------------------------------------------------------------
    @api.model
    @api.private
    def url_de_consentement(self, compte):
        fournisseur = compte.oauth_provider
        fiche = FOURNISSEURS_OAUTH.get(fournisseur)
        identifiant, _secret = self._identifiants(fournisseur)
        if not fiche or not identifiant:
            raise UserError(_(
                "L'authentification %(f)s n'est pas configurée sur cette "
                "instance. Il manque l'inscription d'application et ses "
                "identifiants dans les réglages.",
                f=(fiche or {}).get("libelle", fournisseur or "?")))
        params = {
            "client_id": identifiant,
            "redirect_uri": self._uri_de_retour(),
            "response_type": "code",
            "scope": fiche["portee"],
            "state": self._etat(compte),
            # Préremplir l'écran de consentement avec la bonne adresse évite
            # de lier par erreur la boîte d'une autre session ouverte.
            "login_hint": compte.login or "",
        }
        params.update(fiche.get("extra") or {})
        return fiche["autorisation"] + "?" + urlencode(params)

    @api.model
    @api.private
    def echanger_le_code(self, fournisseur, code):
        return self._appeler_le_jeton(fournisseur, {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self._uri_de_retour(),
        })

    @api.model
    @api.private
    def rafraichir(self, fournisseur, refresh_token):
        return self._appeler_le_jeton(fournisseur, {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        })

    @api.model
    def _appeler_le_jeton(self, fournisseur, corps):
        fiche = FOURNISSEURS_OAUTH.get(fournisseur)
        identifiant, secret = self._identifiants(fournisseur)
        if not fiche or not identifiant or not secret:
            raise UserError(_("Fournisseur OAuth inconnu ou non configuré."))
        donnees = dict(corps, client_id=identifiant, client_secret=secret)
        if fournisseur == "microsoft":
            # Microsoft veut le scope aussi au rafraîchissement, sinon il rend
            # un jeton sans la portée IMAP et la session échoue plus loin,
            # avec un message qui parle d'authentification et non de portée.
            donnees.setdefault("scope", fiche["portee"])
        try:
            rep = requests.post(fiche["jeton"], data=donnees, timeout=DELAI_JETON)
        except requests.RequestException as exc:
            raise UserError(_("Le serveur de jetons est injoignable : %s", exc)) from exc
        if not rep.ok:
            detail = ""
            try:
                charge = rep.json()
                detail = charge.get("error_description") or charge.get("error") or ""
            except ValueError:
                detail = (rep.text or "")[:200]
            raise UserError(_(
                "Le fournisseur a refusé la demande de jeton (%(code)s) : %(d)s",
                code=rep.status_code, d=detail))
        charge = rep.json()
        return {
            "refresh_token": charge.get("refresh_token") or False,
            "access_token": charge.get("access_token") or False,
            "expiration": int(time.time()) + int(charge.get("expires_in") or 0),
        }

    # ------------------------------------------------------------------
    # SASL XOAUTH2
    # ------------------------------------------------------------------
    @api.model
    @api.private
    def chaine_xoauth2(self, login, jeton):
        """La chaîne SASL XOAUTH2, en base64.

        Format commun à Google et à Microsoft :
            base64("user=" + login + ^A + "auth=Bearer " + jeton + ^A^A)
        où ^A est l'octet 0x01.

        ✅ Éprouvée sur le vecteur publié par Microsoft (voir
        `tests/test_autoconfig.py`) : mêmes octets, au caractère près. Sans ce
        contrôle, une erreur d'un seul séparateur donnerait un « échec
        d'authentification » qu'on irait chercher du côté des permissions.
        """
        brut = f"user={login}\x01auth=Bearer {jeton}\x01\x01"
        return base64.b64encode(brut.encode()).decode()
