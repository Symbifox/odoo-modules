"""L'API que consulte l'application Android du coffre de tokens.

Trois choses seulement, parce qu'il n'y a rien d'autre à demander au serveur :
la marque de l'instance, l'appariement, et le contenu **chiffré** du coffre.

🔴 Aucune route ne peut rendre une graine, et ce n'est pas une politique, c'est
un fait : le serveur n'en détient aucune. `secret_cipher` est du chiffré dont la
clé se dérive d'une phrase de passe qui ne lui est jamais envoyée.

⚠️ `save_session=False` partout où l'authentification passe par le jeton
porteur : sans ça, chaque appel d'une application mobile crée une session Odoo
qui ne servira jamais et que rien ne nettoie.
"""
import datetime
import functools
import json
import logging
import re
import urllib.parse

from werkzeug.utils import redirect as wz_redirect

from odoo import fields, http
from odoo.exceptions import AccessError, UserError
from odoo.http import request

_logger = logging.getLogger(__name__)

BASE = "/bf_otp/mobile/v1"
API_VERSION = 1

# Le schéma de lien profond de l'application. Réglable, parce qu'un locataire
# peut publier l'application sous un autre identifiant de paquet.
PARAM_SCHEMAS = "bf_otp.mobile_redirect_schemes"
SCHEMAS_DEFAUT = "com.bluefoxconsultant.otp://"

# Les couleurs livrées par Odoo lui-même. Les laisser passer pour de la marque
# ferait porter le violet d'Odoo à une application qui se veut aux couleurs du
# locataire.
COULEURS_ODOO = {"#714B67", "#875A7B", "#212529", "#017E84"}


def _serialisable(valeur):
    """Rend en JSON ce que l'ORM produit et que `json` ignore.

    🔴 Payé en production le 2026-09-02, sur un vrai téléphone : `read()` rend
    `last_used` en `datetime`, et `json.dumps` lève. Un coffre neuf n'a aucun
    token utilisé, donc `last_used` y vaut `False`, qui se sérialise très bien :
    les essais passaient au vert et la route tombait dès qu'on avait touché un
    seul code. Un compte d'essai vide n'est pas un petit compte, c'est un compte
    qui n'a pas les valeurs qui cassent.

    ISO 8601, et le fuseau reste implicite en UTC comme partout dans Odoo.
    """
    if isinstance(valeur, datetime.datetime):
        return valeur.isoformat(sep=" ")
    if isinstance(valeur, datetime.date):
        return valeur.isoformat()
    raise TypeError("Type non sérialisable : %r" % type(valeur))


def _json(payload, status=200):
    return request.make_response(
        json.dumps(payload, default=_serialisable),
        headers=[("Content-Type", "application/json; charset=utf-8")],
        status=status,
    )


def _corps(**kw):
    """Le corps JSON de la requête, ou les paramètres de formulaire."""
    brut = request.httprequest.get_data(as_text=True)
    if brut:
        try:
            return json.loads(brut)
        except ValueError:
            pass
    return kw


def _redirection_permise(redirect):
    """Vrai quand la cible est un schéma d'application de cette instance.

    🔴 Sans ce contrôle, `/auth/start` est une redirection ouverte qui remet un
    code d'échange vivant à l'URL que l'appelant nomme.
    """
    schemas = (request.env["ir.config_parameter"].sudo().get_param(PARAM_SCHEMAS)
               or SCHEMAS_DEFAUT)
    permis = tuple(s.strip() for s in schemas.split(",") if s.strip())
    return bool(redirect) and bool(permis) and redirect.startswith(permis)


def _authentifie(fn):
    """Résout le jeton porteur, bascule l'environnement, ou répond 401."""
    @functools.wraps(fn)
    def enveloppe(self, *args, **kw):
        entete = request.httprequest.headers.get("Authorization", "")
        jeton = entete[7:].strip() if entete.startswith("Bearer ") else None
        appareil = request.env["bf.otp.device"]._resolve(jeton)
        if not appareil:
            return _json({"error": "unauthorized"}, 401)
        appareil.sudo().write({"last_seen": fields.Datetime.now()})
        request.update_env(user=appareil.user_id.id)
        try:
            # 🔴 Le point de reprise n'est PAS décoratif. Attraper une erreur
            # métier et rendre un JSON au lieu de la laisser remonter empêche
            # Odoo d'annuler la requête : l'écriture refusée reste en base et
            # la transaction se valide quand même à la fin. Mesuré ici : le
            # garde « pas de graine en clair » levait bien, la route répondait
            # bien 400, et le token à graine lisible était bel et bien créé.
            # Une écriture refusée qui persiste est pire qu'une absence de
            # garde, parce qu'elle donne l'illusion d'un refus.
            with request.env.cr.savepoint():
                return fn(self, appareil, *args, **kw)
        except (UserError, AccessError) as exc:
            return _json({"error": str(exc)}, 400)
        except (TypeError, ValueError):
            # ⚠️ Trace complète, pas une ligne d'information. Ces deux
            # exceptions sont presque toujours la faute de l'appelant, mais pas
            # toujours : un défaut de sérialisation d'ici tombe dans la même
            # branche, et le journaliser comme « requête mal formée » l'a fait
            # passer pour une erreur du téléphone pendant qu'il venait du
            # serveur. Payé le 2026-09-02.
            _logger.exception("API mobile du coffre : requête refusée")
            return _json({"error": "bad_request"}, 400)
        except Exception:  # noqa: BLE001
            _logger.exception("API mobile du coffre : erreur inattendue")
            return _json({"error": "server_error"}, 500)
    return enveloppe


def _marque():
    """L'identité visuelle de l'instance, pour que l'application la porte.

    Lue **défensivement**. `report_brand_*` vient de `bluefox_branding`, qui
    peut ne pas être installé ; `primary_color` et `secondary_color` sont des
    champs natifs de `res.company` et servent de repli. ⚠️ Le module s'appelle
    `bluefox_branding` et non `bf_branding`, et sa présence ne se sonde pas par
    `ir.module.module`, que la plupart des comptes ne peuvent pas lire : on lit
    les champs, avec repli.

    Une instance sans aucune marque est une instance normale, pas une erreur :
    l'application possède le défaut Symbifox.

    ⚠️ Les couleurs sont validées en `#RRGGBB` avant d'être envoyées. Une valeur
    mal saisie atteindrait sinon un analyseur de couleur sur le téléphone, et
    « l'application ne s'ouvre plus » est une mauvaise façon d'apprendre que
    quelqu'un a tapé « bleu » dans un champ de configuration.
    """
    societe = request.env.company.sudo()

    def couleur(*noms):
        for nom in noms:
            if nom not in societe._fields:
                continue
            valeur = (societe[nom] or "").strip()
            if (re.fullmatch(r"#[0-9A-Fa-f]{6}", valeur)
                    and valeur.upper() not in COULEURS_ODOO):
                return valeur.upper()
        return None

    return {
        "name": societe.name or "",
        "primary": couleur("report_brand_primary", "primary_color"),
        "dark": couleur("report_brand_dark", "secondary_color"),
        "logo_url": "/web/binary/company_logo",
    }


class BfOtpMobileApi(http.Controller):

    # ── Découverte ────────────────────────────────────────────────────
    @http.route(f"{BASE}/ping", type="http", auth="public", methods=["GET"],
                csrf=False, save_session=False)
    def ping(self, **kw):
        """Sonde publique : l'application s'habille avant toute connexion.

        Public à dessein. Un nom de société et deux couleurs ne révèlent rien :
        le domaine dit déjà à qui appartient ce serveur. En échange,
        l'application n'a pas d'écran gris avant de savoir chez qui elle est.
        """
        module = request.env["ir.module.module"].sudo().search(
            [("name", "=", "bf_otp")], limit=1)
        return _json({
            "ok": True,
            "module": "bf_otp",
            "api": API_VERSION,
            "version": module.installed_version or "",
            "branding": _marque(),
            # ⚠️ L'application ne doit PAS déduire le RP ID de l'adresse
            # saisie : « bluefoxconsultant.com » et « www.bluefoxconsultant.com »
            # sont deux parties de confiance différentes pour WebAuthn, et une
            # clé enrôlée sous l'une n'ouvre rien sous l'autre. Le serveur dit
            # lequel il utilise, une fois pour toutes.
            "rp_id": request.httprequest.host.split(":")[0],
        })

    # ── Appariement ───────────────────────────────────────────────────
    @http.route(f"{BASE}/auth/start", type="http", auth="user", methods=["GET"],
                csrf=False)
    def auth_start(self, **kw):
        """Émet un code à usage unique et rebondit vers l'application.

        🔴 `auth="user"` fait tout le travail demandé : ouverte dans le
        navigateur du téléphone, cette route profite de la session Odoo
        existante. Quelqu'un déjà connecté par Authentik ne voit aucun écran de
        connexion, l'aller-retour est invisible. Sinon Odoo montre sa page de
        connexion, qui mène elle-même à Authentik, puis on revient ici.

        ⚠️ Les échecs repartent par le lien profond en `?error=`, jamais en page
        HTML : une page d'erreur laisserait l'application attendre pour
        toujours un retour qui n'arrive pas.
        """
        redirect = kw.get("redirect") or ""
        state = kw.get("state") or ""
        if not _redirection_permise(redirect):
            return request.make_response(
                "Redirection non autorisée.", status=400,
                headers=[("Content-Type", "text/plain; charset=utf-8")])

        separateur = "&" if "?" in redirect else "?"

        def rebondir(**params):
            requete = urllib.parse.urlencode({**params, "state": state})
            return wz_redirect(f"{redirect}{separateur}{requete}", code=302)

        utilisateur = request.env.user
        if not utilisateur.has_group("bf_otp.group_otp_user"):
            return rebondir(error="no_access")

        code = request.env["bf.otp.device"]._issue_pending(
            utilisateur.id, name=kw.get("device_name"))
        return rebondir(code=code)

    @http.route(f"{BASE}/auth/exchange", type="http", auth="public",
                methods=["POST"], csrf=False, save_session=False)
    def auth_exchange(self, **kw):
        donnees = _corps(**kw)
        appareil = request.env["bf.otp.device"]._exchange(
            (donnees.get("code") or "").strip())
        if not appareil:
            return _json({"error": "invalid_or_expired_code"}, 401)
        request.update_env(user=appareil.user_id.id)
        return _json({
            "token": appareil.sudo().device_token,
            "user_id": appareil.user_id.id,
            "user_name": appareil.user_id.name or "",
        })

    @http.route(f"{BASE}/logout", type="http", auth="public", methods=["POST"],
                csrf=False, save_session=False)
    @_authentifie
    def logout(self, appareil, **kw):
        appareil.sudo().write({"active": False})
        return _json({"ok": True})

    # ── Le coffre, chiffré ────────────────────────────────────────────
    @http.route(f"{BASE}/vault", type="http", auth="public", methods=["GET"],
                csrf=False, save_session=False)
    @_authentifie
    def vault(self, appareil, **kw):
        coffre = request.env["bf.otp.vault"].get_my_vault()
        return _json({"vault": coffre or None, "branding": _marque()})

    @http.route(f"{BASE}/tokens", type="http", auth="public", methods=["GET"],
                csrf=False, save_session=False)
    @_authentifie
    def tokens(self, appareil, **kw):
        return _json({"tokens": request.env["bf.otp.token"].load_my_tokens()})

    # ── Clés d'accès : la MÊME que sur le site ────────────────────────
    @http.route(f"{BASE}/credential/add", type="http", auth="public",
                methods=["POST"], csrf=False, save_session=False)
    @_authentifie
    def credential_add(self, appareil, **kw):
        """Enregistre une clé d'accès enrôlée depuis le téléphone.

        ⚠️ Le scellé arrive tout fait : l'application a dérivé le secret PRF et
        chiffré la clé du coffre avec, exactement comme le navigateur. Le
        serveur ne vérifie aucune signature WebAuthn et n'a pas à le faire, il
        n'accorde aucun droit sur la foi de cette clé.

        🔴 L'identifiant de partie de confiance est le DOMAINE, pas
        l'application : c'est ce qui fait qu'une clé enrôlée ici ouvre aussi le
        coffre depuis le site, et l'inverse. Android ne l'autorise que si le
        domaine publie la déclaration Digital Asset Links correspondante.
        """
        d = _corps(**kw)
        request.env["bf.otp.vault"].add_credential(
            (d.get("name") or "Téléphone").strip(),
            d.get("credential_id"), d.get("prf_salt"),
            d.get("wrapped_secret"), d.get("wrapped_iv"))
        return _json({"ok": True, "vault": request.env["bf.otp.vault"].get_my_vault()})

    @http.route(f"{BASE}/credential/remove", type="http", auth="public",
                methods=["POST"], csrf=False, save_session=False)
    @_authentifie
    def credential_remove(self, appareil, **kw):
        d = _corps(**kw)
        request.env["bf.otp.vault"].remove_credential(int(d.get("id") or 0))
        return _json({"ok": True, "vault": request.env["bf.otp.vault"].get_my_vault()})

    # ── Ajouter un token, lu au code QR sur le téléphone ──────────────
    @http.route(f"{BASE}/token/save", type="http", auth="public",
                methods=["POST"], csrf=False, save_session=False)
    @_authentifie
    def token_save(self, appareil, **kw):
        """Enregistre un token dont la graine a été chiffrée SUR le téléphone.

        🔴 Un code QR `otpauth://` contient la graine en clair. Elle est donc
        lue, chiffrée et effacée dans l'application ; ce qui arrive ici est du
        chiffré, comme tout le reste. Le garde du modèle refuse d'ailleurs
        qu'une graine base32 ou une adresse `otpauth://` entre dans
        `secret_cipher` : si l'application cessait de chiffrer, l'écriture
        échouerait au lieu de remplir la base de graines.

        ⚠️ `save_token` filtre lui-même les champs permis. On ne rajoute pas de
        filtre ici : deux listes de champs autorisées finissent toujours par
        diverger, et c'est celle du modèle qui fait autorité.
        """
        d = _corps(**kw)
        valeurs = d.get("values") if isinstance(d.get("values"), dict) else d
        identifiant = request.env["bf.otp.token"].save_token(
            valeurs, token_id=d.get("token_id") or None)
        return _json({
            "ok": True,
            "id": identifiant,
            "tokens": request.env["bf.otp.token"].load_my_tokens(),
        })

    # ── Usage : ce que « taper pour copier » remonte ──────────────────
    @http.route(f"{BASE}/touch", type="http", auth="public", methods=["POST"],
                csrf=False, save_session=False)
    @_authentifie
    def touch(self, appareil, **kw):
        """Horodate un token qui vient d'être copié.

        ⚠️ Le tri « les plus récents » ne veut rien dire si seul le site
        alimente la date. Sans cette route, copier depuis le téléphone laisserait
        le token au fond de la liste, et l'ordre paraîtrait figé.
        """
        donnees = _corps(**kw)
        request.env["bf.otp.token"].touch_token(int(donnees.get("token_id") or 0))
        return _json({"ok": True})

    @http.route(f"{BASE}/bump", type="http", auth="public", methods=["POST"],
                csrf=False, save_session=False)
    @_authentifie
    def bump(self, appareil, **kw):
        """Avance le compteur d'un token HOTP.

        🔴 Un HOTP est à usage unique : son compteur DOIT avancer là où le code
        est produit, sinon le téléphone et le site rendent éternellement le même
        code et le service le refuse au deuxième usage.
        """
        donnees = _corps(**kw)
        request.env["bf.otp.token"].bump_counter(
            int(donnees.get("token_id") or 0), int(donnees.get("counter") or 0))
        return _json({"ok": True})
