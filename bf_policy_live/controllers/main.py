"""Routes de la clé USB live.

- POST /api/v1/policy/live/profile-key : clé du profil persistant ;
- POST /api/v1/policy/live/login-link : lien de connexion Odoo à usage unique ;
- GET  /odoo?bfos_login=<jeton>        : ouvre ce lien (session Odoo), surcharge
  du client web d'Odoo ; sans le paramètre, /odoo est inchangé.

Contrat de profile-key (voir README.md) :

    Authorization: Bearer <jeton d'accès Authentik émis au client live>
    Corps JSON    : {"create": true|false}

    200 {"key": "<base64 de 32 octets>", "created": true|false}
    400 corps illisible
    401 pas de jeton, jeton refusé par Authentik, ou jeton d'un autre client
    403 personne non autorisée, jeton sans 2FA (`amr` sans `mfa`), profil
        révoqué, ou autre compte Authentik
    404 pas de profil et "create" n'est pas vrai (ou hôte sans politique)
    409 deux créations simultanées : redemander
    500 clé séquestrée illisible (clé de séquestre changée, fiche abîmée)
    503 séquestre non configuré (rien n'est créé), ou aucune politique

L'authentification réutilise celle de bf_policy (_select_org, _resolve_person :
point userinfo d'Authentik, UNE revendication comparée au login, refus des
comptes portail, archivés ou hors société, et du mode de provisionnement de
l'organisation). Elle ajoute un contrôle que bf_policy ne fait pas d'office :
le jeton doit avoir été émis au client OIDC de la clé live, que la vérification
d'audience de l'organisation soit allumée ou non. Un jeton obtenu par une autre
application du même Authentik (Nextcloud, l'installateur…) ne suffit donc pas.
"""
from __future__ import annotations

import json
import logging
import re

import psycopg2

from urllib.parse import urlencode

from odoo import http
from odoo.exceptions import AccessDenied
from odoo.http import request

from odoo.addons.bf_policy.controllers.main import (
    _client_ip,
    _issued_to,
    _json_response,
    _jwt_claims,
    _resolve_person,
    _select_org,
)
from odoo.addons.bf_policy.models import escrow
from odoo.addons.web.controllers.home import Home as WebHome
from odoo.addons.web.controllers.utils import ensure_db

from ..models import live_login
from ..models.live_profile import ProfileRevoked, SubjectMismatch

_logger = logging.getLogger(__name__)

_MAX_BODY = 1024  # {"create": true} et rien d'autre


class _LiveClientOrg:
    """Vue de l'organisation où le « client OIDC » est celui de la clé live.

    `_resolve_person` de bf_policy, quand la vérification d'audience de
    l'organisation est allumée, exige un jeton émis au client d'INSTALLATION :
    il refuserait alors tout jeton de la clé live. On lui présente cette vue,
    qui ne change que `oidc_client_id` et délègue tout le reste (URL userinfo,
    revendication d'identité, is_user_authorized…) à la vraie fiche. Le
    contrôle du client live est de toute façon refait ensuite, sans condition.
    """

    __slots__ = ("_org",)

    def __init__(self, org):
        self._org = org

    def __getattr__(self, name):
        return getattr(self._org, name)

    @property
    def oidc_client_id(self):
        return self._org.live_oidc_client_id or ""


def _bearer() -> str:
    auth = request.httprequest.headers.get("Authorization", "") or ""
    return auth[len("Bearer "):].strip() if auth.startswith("Bearer ") else ""


def _live_caller():
    """Contrôles communs des routes de la clé live.

    Rend ``(user, subject, payload, claims, error_response)`` ; `error_response`
    est vide quand tout passe. Organisation par l'hôte, personne par le point
    userinfo d'Authentik (_resolve_person de bf_policy), jeton émis au client
    live (aud ou azp) et portant `sub`, corps JSON objet de 1 Ko au plus.
    """
    org, error = _select_org()
    if error:
        return None, None, None, None, error
    user, error = _resolve_person(_LiveClientOrg(org))
    if error:
        return None, None, None, None, error

    # Le jeton a passé le point userinfo : ses revendications sont celles
    # qu'Authentik a émises (cf. _jwt_claims de bf_policy). Un jeton opaque
    # n'a pas de revendications lisibles : refusé.
    claims = _jwt_claims(_bearer())
    if not _issued_to(claims, org.live_oidc_client_id or ""):
        _logger.warning(
            "[bf_policy_live] jeton non emis au client live : user=%s ip=%s",
            user.login, _client_ip())
        return None, None, None, None, _json_response(
            {"error": "token not issued to the live key client"}, 401)
    subject = str(claims.get("sub") or "").strip()
    if not subject:
        _logger.warning("[bf_policy_live] jeton sans sub : user=%s ip=%s",
                        user.login, _client_ip())
        return None, None, None, None, _json_response({"error": "token without subject"}, 401)

    raw = request.httprequest.get_data(cache=False)[:_MAX_BODY]
    try:
        payload = json.loads(raw.decode() or "{}")
    except (ValueError, UnicodeDecodeError):
        payload = None
    if not isinstance(payload, dict):
        return None, None, None, None, _json_response({"error": "malformed JSON body"}, 400)
    return user, subject, payload, claims, None


def _has_mfa(claims) -> bool:
    """Vrai si la revendication `amr` du jeton porte `mfa` (liste, ou chaîne
    seule). Authentik copie `amr` de l'id_token dans le jeton d'accès."""
    amr = claims.get("amr")
    if isinstance(amr, str):
        amr = [amr]
    return isinstance(amr, list) and "mfa" in amr


class BfPolicyLiveController(http.Controller):
    @http.route("/api/v1/policy/live/profile-key", type="http", auth="public",
                csrf=False, methods=["POST"], save_session=False)
    def live_profile_key(self, **kwargs):
        user, subject, payload, claims, error = _live_caller()
        if error:
            return error
        # Même raison que le lien de connexion : le client de la clé live est
        # public, la 2FA ne peut pas être laissée à la clé. Le profil déchiffré
        # garde les cookies du navigateur (sessions Odoo et Authentik).
        if not _has_mfa(claims):
            _logger.warning("[bf_policy_live] cle refusee, jeton sans 2FA : "
                            "user=%s ip=%s", user.login, _client_ip())
            return _json_response({"error": "second factor required"}, 403)
        # Seul un vrai booléen crée : "false" (chaîne) ne doit pas valoir oui.
        create = payload.get("create") is True

        if not escrow.available():
            _logger.warning(
                "[bf_policy_live] sequestre indisponible, rien servi : user=%s ip=%s",
                user.login, _client_ip())
            return _json_response({"error": "escrow unavailable"}, 503)

        Profile = request.env["bf.policy.live.profile"].sudo()
        try:
            # Point de reprise : une création simultanée pour la même personne
            # bute sur unique(user_id). On annule la nôtre sans perdre la
            # transaction, et la clé live redemande.
            with request.env.cr.savepoint():
                key, created = Profile._serve_key(user, subject, create)
        except escrow.EscrowUnavailable:
            return _json_response({"error": "escrow unavailable"}, 503)
        except ProfileRevoked:
            _logger.warning("[bf_policy_live] profil revoque demande : user=%s ip=%s",
                            user.login, _client_ip())
            return _json_response({"error": "revoked"}, 403)
        except SubjectMismatch:
            _logger.warning(
                "[bf_policy_live] autre compte Authentik que le createur : "
                "user=%s ip=%s", user.login, _client_ip())
            return _json_response({"error": "identity mismatch"}, 403)
        except LookupError:
            _logger.info("[bf_policy_live] pas de profil : user=%s ip=%s",
                         user.login, _client_ip())
            return _json_response({"error": "no profile"}, 404)
        except psycopg2.IntegrityError:
            request.env.invalidate_all()
            _logger.warning("[bf_policy_live] creation simultanee : user=%s ip=%s",
                            user.login, _client_ip())
            return _json_response({"error": "concurrent request, retry"}, 409)
        except Exception as exc:  # noqa: BLE001 — clé de séquestre changée, fiche abîmée
            # Jamais `exc` en entier dans la réponse ; son type suffit au journal.
            _logger.error("[bf_policy_live] cle illisible : user=%s ip=%s (%s)",
                          user.login, _client_ip(), type(exc).__name__)
            return _json_response({"error": "profile key unreadable"}, 500)

        _logger.info("[bf_policy_live] cle de profil remise : user=%s ip=%s created=%s",
                     user.login, _client_ip(), created)
        return _json_response({"key": key, "created": created}, 200)

    # ------------------------------------------------------- lien de connexion
    @http.route("/api/v1/policy/live/login-link", type="http", auth="public",
                csrf=False, methods=["POST"], save_session=False)
    def live_login_link(self, **kwargs):
        user, _subject, _payload, claims, error = _live_caller()
        if error:
            return error
        # Le lien ouvre une session Odoo SANS la 2FA d'Odoo : la deuxième étape
        # doit donc avoir eu lieu chez Authentik, et c'est Odoo qui le vérifie.
        # Le client de la clé live est public (flux d'appareil, sans secret) :
        # n'importe qui peut mener ce flux sans la clé USB, et un compte sans
        # 2FA inscrite rendrait sinon un jeton `amr: ["pwd"]` suffisant.
        if not _has_mfa(claims):
            _logger.warning("[bf_policy_live] lien refuse, jeton sans 2FA : "
                            "user=%s ip=%s", user.login, _client_ip())
            return _json_response({"error": "second factor required"}, 403)
        # _resolve_person refuse déjà portail et archivés via la politique ; on
        # le redit ici, la session ouverte par ce lien étant une session Odoo.
        if not (user.active and user._is_internal()):
            _logger.warning("[bf_policy_live] lien refuse, usager non interne : "
                            "user=%s ip=%s", user.login, _client_ip())
            return _json_response({"error": "internal users only"}, 403)
        raw = request.env["bf.policy.live.login"].sudo()._issue(user, _client_ip())
        if not raw:
            _logger.warning("[bf_policy_live] trop de liens demandes : user=%s ip=%s",
                            user.login, _client_ip())
            return _json_response({"error": "too many requests"}, 429)
        _logger.info("[bf_policy_live] lien de connexion emis : user=%s ip=%s",
                     user.login, _client_ip())
        root = request.httprequest.url_root.rstrip("/")
        return _json_response({"url": f"{root}/odoo?{LINK_PARAM}={raw}",
                               "expires_in": live_login.TTL_SECONDS}, 200)


# ------------------------------------------------------------ ouverture du lien
# Le lien passe par /odoo lui-même (et non par une route à part) : Brave range
# une fenêtre d'appli selon l'hôte et le CHEMIN de l'URL d'ouverture. Ouvert
# sur /odoo?bfos_login=…, la première fenêtre se range sous l'icône Odoo
# épinglée (https://<hôte>/odoo), comme toutes les suivantes.
LINK_PARAM = "bfos_login"
# Code de la session Authentik du navigateur : empreinte du
# jeton d'accès de la clé, 43 caractères base64url. Odoo ne le vérifie pas : il
# le passe au flux Authentik `bfos-live-navigateur`, qui le vérifie, ouvre la
# session Authentik de Brave et revient sur /odoo.
AUTH_PARAM = "bfos_auth"
AUTH_NAV_PARAM = "bf_policy_live.authentik_nav_url"
_AUTH_RE = re.compile(r"[A-Za-z0-9_-]{43}")


def _session_from_link(token) -> bool:
    """Consomme le lien et ouvre la session Odoo de sa personne. Vrai si la
    session est ouverte ; tout refus est journalisé avec sa raison."""
    Login = request.env["bf.policy.live.login"].sudo()
    rec, reason = Login._peek(token)
    user = rec.user_id if rec else None
    if not reason and not (user.active and user._is_internal()):
        reason = "user not internal or archived"
    # Jamais par-dessus la session d'un AUTRE usager : un membre qui tirerait
    # un lien pour son propre compte et le ferait ouvrir à un collègue
    # basculerait sinon le navigateur de celui-ci sur SON compte (ce que le
    # collègue saisirait ensuite atterrirait chez lui). Le lien n'est pas
    # consommé ; la session ouverte reste.
    # Une session à moitié ouverte (mot de passe tapé, 2FA en attente) compte.
    deja = request.session.uid or request.session.get("pre_uid")
    if not reason and deja and deja != user.id:
        reason = "browser already signed in as another user"
    # Seulement une navigation lancée par le navigateur lui-même (la clé live
    # ouvre Brave sur l'adresse : `Sec-Fetch-Site: none`). Un lien suivi depuis
    # un courriel, Discuss ou une autre page, y compris au bout d'une chaîne
    # de redirections comme /web/session/logout?redirect=…, arrive en
    # `cross-site` ou `same-origin` : refusé, sans consommer. Sans cette
    # garde, déconnecter d'abord le navigateur d'un collègue suffisait à le
    # faire entrer dans le compte de celui qui a tiré le lien.
    if not reason and request.httprequest.headers.get("Sec-Fetch-Site") != "none":
        reason = "not a browser-initiated navigation"
    if reason:
        _logger.warning("[bf_policy_live] lien de connexion refuse (%s) ip=%s",
                        reason, _client_ip())
        return False
    marker = live_login._LINK_ROUTE.set(True)
    try:
        request.session.authenticate(request.db, {
            "type": live_login.LINK_TYPE,
            "login": user.login,
            live_login.TOKEN_KEY: token,
        })
    except AccessDenied:
        # Perdu une course contre une autre ouverture du même lien, ou usager
        # changé entre le coup d'œil et la consommation.
        _logger.warning("[bf_policy_live] lien de connexion refuse "
                        "(consommation impossible) ip=%s", _client_ip())
        return False
    finally:
        live_login._LINK_ROUTE.reset(marker)
    return True


def _authentik_nav_url() -> str:
    """Adresse du flux Authentik de la session du navigateur, ou "" (éteint).
    Paramètre système posé à la main, une adresse https sans requête."""
    url = (request.env["ir.config_parameter"].sudo().get_param(AUTH_NAV_PARAM) or "").strip()
    if not re.fullmatch(r"https://[A-Za-z0-9.-]+(:\d+)?/[A-Za-z0-9/_.-]*", url):
        return ""
    return url


class LiveLoginHome(WebHome):
    """/odoo, /odoo/<chemin> et /web : avec `bfos_login`, ouvre la session du
    lien puis redirige vers la même adresse SANS le paramètre (succès) ou vers
    /web/login (échec). Sans le paramètre : comportement d'Odoo inchangé."""

    @http.route()
    def web_client(self, s_action=None, **kw):
        if LINK_PARAM not in request.httprequest.args:
            return super().web_client(s_action, **kw)
        ensure_db()
        rest = request.httprequest.args.to_dict(flat=False)
        rest.pop(LINK_PARAM, None)
        code = (rest.pop(AUTH_PARAM, None) or [""])[0]
        target = request.httprequest.path
        if rest:
            target += "?" + urlencode(rest, doseq=True)
        token = request.httprequest.args.get(LINK_PARAM)
        ouvert = _session_from_link(token)
        if not ouvert and not request.session.uid:
            return request.redirect("/web/login", 303, local=True)
        nav = _authentik_nav_url()
        if ouvert and nav and _AUTH_RE.fullmatch(code or ""):
            # Seulement juste après un lien consommé : Odoo ne relaie pas un
            # code pour une session déjà ouverte ailleurs.
            return request.redirect(f"{nav}?{urlencode({'c': code})}", 303, local=False)
        # Lien refusé mais session déjà ouverte dans ce navigateur (profil
        # persistant, lien rouvert) : on y reste, sans le paramètre.
        return request.redirect(target, 303, local=True)
