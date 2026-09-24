"""API mobile de la messagerie SMS — consommée par l'app Android native.

Contrat REST/JSON propre (pas le JSON-RPC ``call_kw`` d'Odoo) sous
``/bf_sms_archive/mobile/v1/``. Auth par jeton porteur :

  1. POST /login {login, password}  → { token, ... }  (jeton d'appareil)
  2. Requêtes suivantes : en-tête ``Authorization: Bearer <token>``.

Chaque requête authentifiée s'exécute DANS le contexte de l'utilisateur de
l'appareil (``request.update_env(user=…)``), donc les règles d'accès par
propriétaire des fils s'appliquent telles quelles.
"""
import base64
import binascii
import functools
import json
import re
import logging
import threading
import time
import urllib.parse
from collections import defaultdict

from markupsafe import Markup
from werkzeug.utils import redirect as wz_redirect

from odoo import fields, http
from odoo.exceptions import AccessDenied, UserError
from odoo.http import request
from odoo.service.model import PG_CONCURRENCY_EXCEPTIONS_TO_RETRY

from ..models.push_transport import parse_push_keys, webpush_available
from .voipms_webhook import _safe_media_url

_logger = logging.getLogger(__name__)

# ── Péremption locale : le coupe-circuit qui n'a besoin de personne ──────
#
# 🔴 Un jeton refusé rend 401, et l'application efface alors
# tout ce qu'elle garde. Mais le 401 n'arrive qu'au PROCHAIN appel : un
# téléphone en mode avion, ou une application jamais rouverte, garde ses
# données indéfiniment. C'est le seul cas que ni le 401 ni un message poussé ne
# couvrent, et c'est précisément celui d'un téléphone qui part avec la personne.
#
# Le serveur annonce donc un délai, et l'application s'efface d'elle-même au
# bout de ce délai SANS contact authentifié réussi. Le compteur ne se remet à
# zéro que sur une réponse authentifiée : `/ping` est public, le relire ne
# prouve rien et ne doit rien rallonger.
#
# ⚠️ La clé est PARTAGÉE par les cinq surfaces mobiles de la maison et
# recopiée dans chacune plutôt que mise en commun : aucun de ces modules ne
# dépend des autres, et le coffre de tokens surtout pas. Une valeur, cinq
# lecteurs, zéro dépendance.
CLE_PEREMPTION = "bf_mobile.wipe_after_days"
PEREMPTION_DEFAUT = 30


def _peremption_locale(env):
    """Jours sans contact authentifié au bout desquels l'app s'efface.

    Rend 0 quand la garde est volontairement désarmée.

    🔴 Clé ABSENTE n'est PAS zéro. Un paramètre jamais posé doit rendre le
    défaut, sinon la garde serait désarmée partout où personne n'a rien
    configuré, c'est-à-dire partout. L'état qu'on obtient sans rien faire doit
    être l'état sûr.
    """
    brut = env["ir.config_parameter"].sudo().get_param(CLE_PEREMPTION)
    if brut is None or brut is False or str(brut).strip() == "":
        return PEREMPTION_DEFAUT
    try:
        jours = int(str(brut).strip())
    except (TypeError, ValueError):
        _logger.warning(
            "Péremption locale : %r n'est pas un nombre de jours, "
            "le défaut de %s s'applique.", brut, PEREMPTION_DEFAUT)
        return PEREMPTION_DEFAUT
    return max(jours, 0)


BASE = "/bf_sms_archive/mobile/v1"
SMS_USER_GROUP = "bf_sms_archive.group_sms_user"
REDIRECT_SCHEMES_PARAM = "bf_sms_archive.mobile_redirect_schemes"
DEFAULT_REDIRECT_SCHEMES = "odoosms://"  # BF ajoute son schéma via l'ICP

# ── Anti-bourrage sur /login ──────────────────────────────────────────────────
# Cette route valide un mot de passe hors du parcours /web/login. Sans plafond,
# c'est un banc d'essai d'identifiants pour TOUTE l'instance, sans session ni
# cookie (save_session=False) donc sans trace côté sessions. Deux compteurs :
# par IP (une source qui balaie beaucoup de comptes) et par identifiant (un
# botnet distribué qui vise un seul compte).
_login_lock = threading.Lock()
_login_ip_data = defaultdict(list)
_login_id_data = defaultdict(list)
_LOGIN_IP_MAX = 20
_LOGIN_ID_MAX = 8
_LOGIN_WINDOW = 900  # 15 min
_MAX_TRACKED_KEYS = 10000


def _login_ip():
    """Socket peer only — jamais X-Forwarded-For (en-tête contrôlé par l'appelant,
    qui donnerait un compteur neuf à chaque requête)."""
    try:
        return request.httprequest.remote_addr or "unknown"
    except Exception:
        return "unknown"


def _login_rate_ok(login):
    """Check-and-record atomique sur les deux compteurs."""
    now = time.monotonic()
    cutoff = now - _LOGIN_WINDOW
    ip = _login_ip()
    key = (login or "").strip().lower()[:128]
    with _login_lock:
        for store in (_login_ip_data, _login_id_data):
            if len(store) > _MAX_TRACKED_KEYS:
                store.clear()
        ip_hits = [t for t in _login_ip_data[ip] if t > cutoff]
        id_hits = [t for t in _login_id_data[key] if t > cutoff]
        if len(ip_hits) >= _LOGIN_IP_MAX or len(id_hits) >= _LOGIN_ID_MAX:
            _login_ip_data[ip], _login_id_data[key] = ip_hits, id_hits
            return False
        ip_hits.append(now)
        id_hits.append(now)
        _login_ip_data[ip], _login_id_data[key] = ip_hits, id_hits
        return True


def _push_config():
    """Config push retournée à l'app : base ntfy + jeton de lecture pour le
    distributeur EMBARQUÉ (repli quand aucune app ntfy n'est installée). Vide si
    non configuré → l'app se rabat sur le distributeur externe seulement."""
    icp = request.env["ir.config_parameter"].sudo()
    return {
        "ntfy_base_url": icp.get_param("bf_sms_archive.ntfy_base_url") or "",
        "ntfy_read_token": icp.get_param("bf_sms_archive.ntfy_read_token") or "",
    }


def _allowed_redirect(redirect):
    """Vrai si l'URL de redirection commence par un schéma d'app autorisé
    (anti open-redirect / exfiltration de code)."""
    schemes = (request.env["ir.config_parameter"].sudo().get_param(
        REDIRECT_SCHEMES_PARAM) or DEFAULT_REDIRECT_SCHEMES)
    allowed = tuple(s.strip() for s in schemes.split(",") if s.strip())
    return bool(redirect) and redirect.startswith(allowed)


def _json(data, status=200):
    return request.make_response(
        json.dumps(data, default=str),
        headers=[("Content-Type", "application/json; charset=utf-8")],
        status=status,
    )


# ── Page d'accord à l'appariement (S-M1) ─────────────────────────────────────
# Dupliquée dans ``bf_email_management`` plutôt que partagée, comme
# ``_branding`` : les deux modules s'installent l'un sans l'autre.
_ENTETES_ACCORD = [
    ("Content-Type", "text/html; charset=utf-8"),
    # Jamais dans un cadre : une page tierce qui l'encadrerait en transparence
    # ferait toucher « Autoriser » à l'aveugle (détournement de clic).
    ("X-Frame-Options", "DENY"),
    # ⚠️ Pas de `form-action` : Chrome l'applique aussi à la REDIRECTION qui
    # suit l'envoi du formulaire, et le retour vers le schéma de l'app serait
    # bloqué sans un mot.
    ("Content-Security-Policy",
     "frame-ancestors 'none'; default-src 'none'; style-src 'unsafe-inline'"),
    # La page porte le jeton CSRF de la session.
    ("Cache-Control", "no-store"),
    ("Referrer-Policy", "no-referrer"),
]

_STYLE_ACCORD = Markup(
    "body{margin:0;background:#f4f5f7;color:#1f2328;"
    "font:16px/1.5 system-ui,-apple-system,'Segoe UI',Roboto,sans-serif}"
    "main{max-width:26rem;margin:0 auto;padding:2rem 1.25rem}"
    "h1{font-size:1.35rem;line-height:1.3;margin:0 0 1.25rem}"
    "dl{background:#fff;border:1px solid #d8dce1;border-radius:.5rem;"
    "padding:.25rem 1rem;margin:0 0 1.25rem}"
    "dt{font-size:.8rem;color:#57606a;margin-top:.75rem}"
    "dd{margin:0 0 .75rem;overflow-wrap:anywhere}"
    "small{color:#57606a}"
    "p{margin:0 0 1.5rem;color:#3d444d}"
    "button{display:block;width:100%;font:inherit;font-weight:600;"
    "padding:.8rem;border-radius:.5rem;margin-bottom:.75rem;cursor:pointer}"
    ".oui{background:#1f2328;color:#fff;border:1px solid #1f2328}"
    ".non{background:#fff;color:#1f2328;border:1px solid #8c959f}"
)


def _rebondir(demande, **params):
    """Retour vers le lien profond de l'app, ``state`` toujours joint.

    Le refus et le défi manquant reviennent par ce chemin plutôt que par une
    page : l'app enchaîne les deux modules dans une même session de
    navigateur, et une page sans issue sur la première étape bloquerait toute
    la connexion. (Restent des pages : la redirection non autorisée, qu'on ne
    peut pas suivre, et le compte hors du groupe SMS, comme avant.) 303 après
    le POST de la page d'accord, pour que le navigateur ne renvoie pas le
    formulaire.
    """
    redirect = demande["redirect"]
    sep = "&" if "?" in redirect else "?"
    query = urllib.parse.urlencode({**params, "state": demande["state"]})
    code = 303 if request.httprequest.method == "POST" else 302
    return wz_redirect(f"{redirect}{sep}{query}", code=code)


def _page_accord(demande):
    """La page qui dit à la personne ce qu'elle s'apprête à autoriser.

    Sans JavaScript, lisible sur un téléphone, dans la langue de l'usager.
    Rien de secret n'y figure : ni jeton, ni code, ni mot de passe SIP. Toute
    valeur venue de l'URL est échappée (``Markup`` échappe ce qu'on lui
    interpole).
    """
    env = request.env
    user = env.user
    champs = Markup("").join(
        Markup('<input type="hidden" name="%s" value="%s"/>') % (nom, demande[nom])
        for nom in ("redirect", "state", "code_challenge",
                    "code_challenge_method", "device_name"))
    appareil = Markup("")
    if demande["device_name"]:
        appareil = Markup("<dt>%s</dt><dd>%s</dd>") % (
            env._("Device"), demande["device_name"])
    page = Markup(
        '<!DOCTYPE html><html lang="%(lang)s"><head><meta charset="utf-8"/>'
        '<meta name="viewport" content="width=device-width, initial-scale=1"/>'
        '<meta name="robots" content="noindex"/>'
        "<title>Symbifox Mobile</title><style>%(style)s</style></head>"
        "<body><main><h1>%(titre)s</h1>"
        "<dl><dt>%(l_compte)s</dt><dd>%(nom)s<br/><small>%(login)s</small></dd>"
        "%(appareil)s"
        "<dt>%(l_acces)s</dt><dd>%(acces)s</dd></dl>"
        "<p>%(avis)s</p>"
        '<form method="post" action="%(action)s">'
        '<input type="hidden" name="csrf_token" value="%(csrf)s"/>%(champs)s'
        '<button class="oui" type="submit" name="decision" value="allow">%(oui)s</button>'
        '<button class="non" type="submit" name="decision" value="deny">%(non)s</button>'
        "</form></main></body></html>"
    ) % {
        "lang": (env.lang or "en_US").split("_")[0],
        "style": _STYLE_ACCORD,
        "titre": env._("Symbifox Mobile wants to access your account"),
        "l_compte": env._("Account"),
        "nom": user.name or "",
        "login": user.login or "",
        "appareil": appareil,
        "l_acces": env._("Access requested"),
        "acces": env._("Your SMS messages and calls"),
        "avis": env._("Only allow this if you just started signing in from the "
                      "app on your phone."),
        "action": f"{BASE}/auth/consent",
        "csrf": request.csrf_token(),
        "champs": champs,
        "oui": env._("Allow"),
        "non": env._("Deny"),
    }
    return request.make_response(page, headers=_ENTETES_ACCORD)


def _body():
    try:
        raw = request.httprequest.get_data(as_text=True) or "{}"
        return json.loads(raw)
    except (ValueError, TypeError):
        return {}


# ── MMS sortant depuis l'app ─────────────────────────────────────────────────
# Trois pièces, parce que VOIP.ms n'expose que ``media1``..``media3`` sur
# ``sendMMS`` — au-delà, la quatrième partirait dans le vide sans erreur. Les
# plafonds de taille sont ceux du MMS chez les transporteurs nord-américains
# (~1 Mo par pièce, ~2 Mo l'enveloppe) : l'app redimensionne AVANT d'envoyer,
# et ce garde-fou refuse clairement plutôt que de laisser VOIP.ms répondre un
# statut opaque une fois le message déjà créé.
MMS_MAX_PARTS = 3
MMS_MAX_PART_BYTES = 1_000_000
MMS_MAX_TOTAL_BYTES = 2_000_000
_MIME_RE = re.compile(r"^[\w.+-]+/[\w.+-]+$")


def _parse_media(raw):
    """Liste de {filename, content_type, data_b64} validée, ou lève UserError.

    Rend une liste vide quand rien n'est joint : ``action_send`` traite alors
    l'envoi comme un SMS, exactement comme avant.
    """
    if not raw:
        return []
    if not isinstance(raw, list):
        raise UserError("Pièces jointes invalides.")
    if len(raw) > MMS_MAX_PARTS:
        raise UserError("Maximum %d pièces jointes." % MMS_MAX_PARTS)
    out = []
    total = 0
    for item in raw:
        if not isinstance(item, dict):
            raise UserError("Pièce jointe invalide.")
        data_b64 = (item.get("data_b64") or "").strip()
        if not data_b64:
            raise UserError("Pièce jointe vide.")
        try:
            size = len(base64.b64decode(data_b64, validate=True))
        except (ValueError, binascii.Error):
            raise UserError("Pièce jointe illisible.")
        if not size:
            raise UserError("Pièce jointe vide.")
        if size > MMS_MAX_PART_BYTES:
            raise UserError("Pièce jointe trop volumineuse (max 1 Mo).")
        total += size
        if total > MMS_MAX_TOTAL_BYTES:
            raise UserError("Pièces jointes trop volumineuses (max 2 Mo au total).")
        content_type = (item.get("content_type") or "").strip()
        if not _MIME_RE.match(content_type):
            content_type = "application/octet-stream"
        # Le nom ne sert qu'à l'archivage local : on le borne et on retire tout
        # séparateur de chemin, il finit dans une pièce jointe Odoo.
        filename = (item.get("filename") or "fichier").strip()
        filename = re.sub(r"[/\\]", "_", filename)[:128] or "fichier"
        out.append({
            "filename": filename,
            "content_type": content_type,
            "data_b64": data_b64,
        })
    return out


def _authed(fn):
    """Résout le jeton porteur → charge l'appareil → bascule l'env sur son
    utilisateur. 401 si absent/invalide."""
    @functools.wraps(fn)
    def wrapper(self, *args, **kw):
        header = request.httprequest.headers.get("Authorization", "")
        token = header[7:].strip() if header.startswith("Bearer ") else None
        device = request.env["sms.archive.mobile.device"]._resolve(token)
        if not device:
            return _json({"error": "unauthorized"}, 401)
        # Hors transaction et au plus une fois la minute : voir le modèle.
        device._touch_last_seen()
        request.update_env(user=device.user_id.id)
        try:
            return fn(self, device, *args, **kw)
        except PG_CONCURRENCY_EXCEPTIONS_TO_RETRY:
            # ⚠️ Pas à nous. Sur un conflit d'écriture, Odoo rejoue la requête
            # entière (``service.model.retrying``, jusqu'à cinq fois) — mais
            # seulement si l'exception lui parvient. L'attraper ci-dessous en
            # « erreur inattendue » changeait un conflit d'une milliseconde en
            # 500 définitif, et l'app annulait le geste.
            raise
        except UserError as exc:
            return _json({"error": str(exc)}, 400)
        except Exception:  # noqa: BLE001
            _logger.exception("Mobile API : erreur inattendue")
            return _json({"error": "server_error"}, 500)
    return wrapper


# Odoo ships these on every fresh database. A company still wearing them has
# not chosen anything, so treating them as "the tenant's brand" would paint the
# app in stock Odoo purple — which is precisely the look this product exists to
# avoid. Treated as unbranded, so the product default applies instead.
ODOO_STOCK_COLOURS = {"#714B67", "#875A7B", "#212529", "#017E84"}


def _branding():
    """Marque de l'instance, pour que l'app porte les couleurs du locataire.

    Dupliqué depuis ``bf_email_management`` — quinze lignes — plutôt que d'y
    ajouter une dépendance : les deux modules sont installables séparément et
    l'un ne doit pas exiger l'autre pour servir sa propre sonde.

    Lecture défensive : ``report_brand_*`` vient de ``bluefox_branding``,
    absent de bien des instances ; à défaut les champs natifs de
    ``res.company`` ; à défaut rien, et l'app applique ses propres valeurs.
    """
    company = request.env.company.sudo()

    def hex_colour(*names):
        for name in names:
            if name not in company._fields:
                continue
            value = (company[name] or "").strip()
            if re.fullmatch(r"#[0-9A-Fa-f]{6}", value) \
                    and value.upper() not in ODOO_STOCK_COLOURS:
                return value.upper()
        return None

    return {
        "name": company.name or "",
        "primary": hex_colour("report_brand_primary", "primary_color"),
        "dark": hex_colour("report_brand_dark", "secondary_color"),
        "logo_url": "/web/binary/company_logo",
    }


class BfSmsMobileApi(http.Controller):

    # ── Découverte ────────────────────────────────────────────────────
    @http.route(f"{BASE}/ping", type="http", auth="public", methods=["GET"],
                csrf=False, save_session=False)
    def ping(self, **kw):
        """Sonde de capacité, jumelle de celle de ``bf_email_management``.

        Son absence était un défaut, pas un manque : l'app interroge ``/ping``
        sur les DEUX modules pour décider des onglets à offrir. Sans réponse
        ici, une installation NEUVE ne voyait pas la moitié SMS et n'en
        demandait jamais le jeton — le symétrique exact du défaut côté
        courriel, masqué jusqu'ici parce qu'un jeton SMS déjà présent suffit
        à faire apparaître l'onglet.
        """
        module = request.env["ir.module.module"].sudo().search(
            [("name", "=", "bf_sms_archive")], limit=1)
        return _json({
            "ok": True,
            "module": "bf_sms_archive",
            # Le délai de péremption locale : voir `_peremption_locale`.
            "wipe_after_days": _peremption_locale(request.env),
            "api": 1,
            "version": module.installed_version or "",
            "branding": _branding(),
        })

    # ── Auth ──────────────────────────────────────────────────────────
    @http.route(f"{BASE}/login", type="http", auth="public", methods=["POST"],
                csrf=False, save_session=False)
    def login(self, **kw):
        """⚠ Chemin hérité : mot de passe seul, hors du parcours /web/login.

        Préférer ``/auth/start``, qui délègue à /web/login et récupère donc le
        SSO Authentik ET le second facteur. Ce qui est verrouillé ici :

        - plafond par IP et par identifiant (voir _login_rate_ok) ;
        - réponse d'échec UNIFORME. Le contrôle de groupe venait après
          l'authentification avec un code distinct (401 vs 403), ce qui faisait
          de la route un validateur de couples identifiant/mot de passe pour
          n'importe quel compte de l'instance, SMS ou non. La vraie raison part
          au journal serveur, pas au client ;
        - refus quand le compte porte un TOTP actif : un chemin sans second
          facteur ne doit pas émettre un jeton porteur durable pour un compte
          protégé par MFA.
        """
        data = _body()
        login = (data.get("login") or "").strip()
        password = data.get("password") or ""
        if not login or not password:
            return _json({"error": "missing_credentials"}, 400)
        if not _login_rate_ok(login):
            _logger.warning(
                "Mobile API : plafond de connexion atteint (IP %s, identifiant %s)",
                _login_ip(), login[:64])
            return _json({"error": "rate_limited"}, 429)
        credential = {"type": "password", "login": login, "password": password}
        try:
            auth_info = request.env["res.users"].sudo().authenticate(
                request.db, credential, {"interactive": False})
        except AccessDenied:
            return _json({"error": "invalid_credentials"}, 401)
        uid = auth_info["uid"] if isinstance(auth_info, dict) else auth_info
        user = request.env["res.users"].sudo().browse(uid)
        # MFA : auth_totp impose le second facteur dans /web/login, pas dans
        # authenticate(). Renvoyer l'app vers /auth/start plutôt que de contourner.
        if "totp_enabled" in user._fields and user.totp_enabled:
            _logger.info(
                "Mobile API : connexion par mot de passe refusée pour %s "
                "(TOTP actif) — utiliser /auth/start", user.login)
            return _json({"error": "mfa_required",
                          "auth_start": f"{BASE}/auth/start"}, 403)
        if not user.has_group(SMS_USER_GROUP):
            # Réponse identique à un mot de passe erroné : pas d'oracle.
            _logger.info(
                "Mobile API : identifiants valides mais compte hors du groupe "
                "SMS (%s) — réponse uniforme", user.login)
            return _json({"error": "invalid_credentials"}, 401)
        device = request.env["sms.archive.mobile.device"]._issue(
            uid, name=data.get("device_name"), platform=data.get("platform", "android"))
        # Remis une fois, puis scellé : seule l'empreinte reste en base.
        jeton = device.device_token
        device._seal()
        request.update_env(user=uid)
        return _json({
            "token": jeton,
            "user_id": uid,
            "user_name": user.name,
            "lines": request.env["sms.archive.thread"].get_lines(),
            "config": request.env["sms.archive.thread"].get_messenger_config(),
        })

    # ── Auth par connexion web (capture le login Odoo : mot de passe, SSO
    #    Authentik « les sessions SSO », MFA — tout ce que la page /web/login offre) ──
    #
    # 🔴 Deux temps depuis l'audit du 2026-09-08 (S-M1). Le GET émettait le code
    # d'appariement et repartait aussitôt vers le schéma de l'app : une app
    # tierce du téléphone qui déclare ce schéma pouvait ouvrir l'URL dans le
    # navigateur où la personne est déjà connectée et apparier un appareil sans
    # qu'elle voie quoi que ce soit. PKCE n'y peut rien, puisque c'est cette
    # app-là qui a fabriqué le défi. Le GET montre donc ce qui est demandé ; seul
    # un POST « Autoriser », jeton CSRF compris, émet le code.
    @http.route(f"{BASE}/auth/start", type="http", auth="user", methods=["GET"],
                csrf=False)
    def auth_start(self, **kw):
        """Ouverte dans un onglet du navigateur. ``auth='user'`` → si non
        connecté, Odoo redirige vers /web/login (mot de passe OU boutons SSO),
        puis revient ici authentifié. On valide la demande, puis on REND la page
        d'accord : aucun code n'est émis ici. Le vrai jeton n'apparaît jamais."""
        erreur, demande = self._appariement(kw)
        if erreur is not None:
            return erreur
        return _page_accord(demande)

    @http.route(f"{BASE}/auth/consent", type="http", auth="user",
                methods=["POST"], csrf=True)
    def auth_consent(self, **kw):
        """Réponse de la page d'accord. ``csrf=True`` : sans le jeton de la
        session, Odoo refuse le POST avant de nous appeler.

        ⚠️ Tout est REVALIDÉ : les champs cachés viennent du navigateur, donc de
        n'importe qui. L'usager, lui, vient de la session, jamais du formulaire.
        """
        erreur, demande = self._appariement(kw)
        if erreur is not None:
            return erreur
        if kw.get("decision") != "allow":
            _logger.info("Mobile API : appariement refusé par %s sur la page "
                         "d'accord", request.env.user.login)
            return _rebondir(demande, error="access_denied")
        code = request.env["sms.archive.mobile.device"]._issue_pending(
            request.env.user.id, name=demande["device_name"],
            challenge=demande["code_challenge"])
        return _rebondir(demande, code=code)

    def _appariement(self, kw):
        """Valide une demande d'appariement : ``(réponse d'erreur, None)`` ou
        ``(None, demande)``. UNE définition pour le GET et le POST, sans quoi
        la page d'accord finirait par accepter ce que la page d'ouverture
        refuse."""
        redirect = kw.get("redirect") or ""
        state = kw.get("state") or ""
        if not _allowed_redirect(redirect):
            return request.make_response(
                "Redirection non autorisée.", status=400,
                headers=[("Content-Type", "text/plain; charset=utf-8")]), None
        user = request.env.user
        if not user.has_group(SMS_USER_GROUP):
            return request.make_response(
                "Ce compte n'a pas accès à la messagerie SMS.", status=403,
                headers=[("Content-Type", "text/plain; charset=utf-8")]), None
        demande = {
            "redirect": redirect,
            "state": state,
            "code_challenge": (kw.get("code_challenge") or "").strip(),
            "code_challenge_method": (kw.get("code_challenge_method") or "S256").upper(),
            "device_name": (kw.get("device_name") or "").strip()[:80],
        }

        # 🔴 PKCE obligatoire. Un schéma d'application personnalisé n'est pas
        # exclusif sur Android : sans défi, un code intercepté par une autre
        # application s'échangerait contre un jeton porteur sur la messagerie
        # SMS de la personne.
        #
        # ⚠️ Aucune période de grâce, et c'est délibéré : les
        # appareils déjà appariés gardent leur jeton et ne repassent jamais par
        # l'échange. Seule une NOUVELLE connexion lancée depuis un APK d'avant
        # le lot casse, et elle casse bruyamment, ici, avec un motif lisible.
        if not demande["code_challenge"] or demande["code_challenge_method"] != "S256":
            _logger.info(
                "Mobile API : appariement refusé, défi PKCE absent ou méthode "
                "%s non acceptée", demande["code_challenge_method"])
            return _rebondir(demande, error="pkce_required"), None
        return None, demande

    @http.route(f"{BASE}/auth/exchange", type="http", auth="public",
                methods=["POST"], csrf=False, save_session=False)
    def auth_exchange(self, **kw):
        """L'app échange le code unique (reçu par deep-link) contre le jeton
        porteur durable, sur HTTPS."""
        data = _body()
        device = request.env["sms.archive.mobile.device"]._exchange(
            (data.get("code") or "").strip(),
            (data.get("code_verifier") or "").strip())
        if not device:
            return _json({"error": "invalid_or_expired_code"}, 401)
        if data.get("fcm_token"):
            device.sudo().write({"fcm_token": data["fcm_token"].strip()})
        # Remis une fois, puis scellé : seule l'empreinte reste en base.
        jeton = device.device_token
        device._seal()
        request.update_env(user=device.user_id.id)
        return _json({
            "token": jeton,
            "user_id": device.user_id.id,
            "user_name": device.user_id.name,
            "lines": request.env["sms.archive.thread"].get_lines(),
            "config": request.env["sms.archive.thread"].get_messenger_config(),
            "push": _push_config(),
        })

    @http.route(f"{BASE}/logout", type="http", auth="public", methods=["POST"],
                csrf=False, save_session=False)
    @_authed
    def logout(self, device, **kw):
        # L'endpoint part aussi, et ses clés WebPush avec lui (voir ``write``
        # du modèle) : un appareil déconnecté n'a plus rien à recevoir.
        device.sudo().write({"active": False, "fcm_token": False,
                             "push_endpoint": False})
        return _json({"ok": True})

    # ── Bootstrap ─────────────────────────────────────────────────────
    @http.route(f"{BASE}/config", type="http", auth="public", methods=["GET"],
                csrf=False, save_session=False)
    @_authed
    def config(self, device, **kw):
        Thread = request.env["sms.archive.thread"]
        return _json({
            "user_name": device.user_id.name,
            "lines": Thread.get_lines(),
            "config": Thread.get_messenger_config(),
            "unread": Thread.get_unread_summary(),
            "push": _push_config(),
        })

    # ── Fils ──────────────────────────────────────────────────────────
    @http.route(f"{BASE}/threads", type="http", auth="public", methods=["GET"],
                csrf=False, save_session=False)
    @_authed
    def threads(self, device, **kw):
        archived = kw.get("archived") in ("1", "true", "True")
        line_id = int(kw["line_id"]) if kw.get("line_id") else None
        threads = request.env["sms.archive.thread"].get_messenger_threads(
            archived=archived, search=kw.get("search") or None, line_id=line_id)
        return _json({"threads": threads})

    @http.route(f"{BASE}/conversation", type="http", auth="public", methods=["GET"],
                csrf=False, save_session=False)
    @_authed
    def conversation(self, device, **kw):
        thread_id = kw.get("thread_id")
        if not thread_id:
            return _json({"error": "missing_thread_id"}, 400)
        before_id = int(kw["before_id"]) if kw.get("before_id") else None
        data = request.env["sms.archive.thread"].get_conversation(
            int(thread_id), before_id=before_id)
        return _json(data)

    # ── Actions ───────────────────────────────────────────────────────
    @http.route(f"{BASE}/send", type="http", auth="public", methods=["POST"],
                csrf=False, save_session=False)
    @_authed
    def send(self, device, **kw):
        data = _body()
        body = data.get("body") or ""
        line_id = data.get("line_id")
        media = _parse_media(data.get("media"))
        Thread = request.env["sms.archive.thread"]
        Msg = request.env["sms.archive.message"]
        if data.get("thread_id"):
            thread = Thread.with_context(active_test=False).browse(int(data["thread_id"]))
            if not thread.exists():
                return _json({"error": "thread_not_found"}, 404)
            thread._check_messenger_access()
            dst = thread.phone_normalized
            if not line_id:
                # ligne par défaut du dernier message du fil, sinon 1re ligne
                last = thread.message_ids.filtered(lambda m: m.line_id)[:1]
                line_id = last.line_id.id if last else (
                    request.env["sms.archive.line"]._lines_for_user(
                        device.user_id)[:1].id)
        else:
            dst = data.get("phone")
        if not (dst and line_id):
            return _json({"error": "missing_destination_or_line"}, 400)
        # Un MMS peut n'avoir aucun texte — une photo seule est un message
        # complet. Le corps vide ne se refuse donc que sur le chemin SMS.
        if not (body.strip() or media):
            return _json({"error": "empty_message"}, 400)
        # ⚠️ La ligne sait-elle faire un MMS ? ``action_send`` pose bien la
        # question, mais DANS son bloc `try` : l'erreur y est rattrapée, le
        # message créé quand même en « échoué », et la route répondrait « ok ».
        # L'app enchaînerait sur le fil, où la photo apparaîtrait partie alors
        # qu'elle n'a jamais quitté l'appareil. On tranche donc avant, pendant
        # qu'il n'y a encore rien à défaire.
        if media:
            line = request.env["sms.archive.line"]._lines_for_user(
                device.user_id).filtered(lambda l: l.id == int(line_id))
            if not line:
                return _json({"error": "line_not_found"}, 404)
            if not line.mms_enabled:
                raise UserError(
                    "Cette ligne n'envoie pas de MMS : retirez la pièce jointe "
                    "ou choisissez une autre ligne.")
        msg_id = Msg.action_send(int(line_id), dst, body, media=media or None)
        msg = Msg.browse(msg_id)
        return _json({
            "ok": True,
            "thread_id": msg.thread_id.id,
            "message": msg._messenger_dict(),
        })

    @http.route(f"{BASE}/mark_read", type="http", auth="public", methods=["POST"],
                csrf=False, save_session=False)
    @_authed
    def mark_read(self, device, **kw):
        data = _body()
        if not data.get("thread_id"):
            return _json({"error": "missing_thread_id"}, 400)
        summary = request.env["sms.archive.thread"].mark_thread_read(int(data["thread_id"]))
        return _json({"ok": True, "unread": summary})

    # ── Archivage / épinglage / contacts (parité avec la SPA web) ─────
    @http.route(f"{BASE}/thread/archive", type="http", auth="public",
                methods=["POST"], csrf=False, save_session=False)
    @_authed
    def thread_archive(self, device, **kw):
        data = _body()
        if not data.get("thread_id"):
            return _json({"error": "missing_thread_id"}, 400)
        request.env["sms.archive.thread"].messenger_set_archived(
            int(data["thread_id"]), bool(data.get("archived", True)))
        return _json({"ok": True})

    @http.route(f"{BASE}/thread/pin", type="http", auth="public",
                methods=["POST"], csrf=False, save_session=False)
    @_authed
    def thread_pin(self, device, **kw):
        data = _body()
        if not data.get("thread_id"):
            return _json({"error": "missing_thread_id"}, 400)
        pinned = request.env["sms.archive.thread"].messenger_toggle_pin(
            int(data["thread_id"]))
        return _json({"ok": True, "is_pinned": pinned})

    @http.route(f"{BASE}/contacts", type="http", auth="public",
                methods=["GET"], csrf=False, save_session=False)
    @_authed
    def contacts(self, device, **kw):
        """Recherche de contacts pour composer un nouveau SMS."""
        term = (kw.get("q") or "").strip()
        if len(term) < 2:
            return _json({"contacts": []})
        partners = request.env["res.partner"].search_read(
            ["|", "|", ("name", "ilike", term),
             ("phone", "ilike", term), ("mobile", "ilike", term)],
            ["name", "phone", "mobile"], limit=20, order="name")
        return _json({"contacts": partners})

    # ── Push FCM ──────────────────────────────────────────────────────
    @http.route(f"{BASE}/register_push", type="http", auth="public", methods=["POST"],
                csrf=False, save_session=False)
    @_authed
    def register_push(self, device, **kw):
        """Enregistre l'endpoint UnifiedPush (ntfy) de l'app pour cet appareil.

        Corps : ``{endpoint, app_version, p256dh, auth}``. Les deux clés de
        l'abonnement WebPush sont FACULTATIVES : absentes (app ≤ 2.41.0), les
        clés déjà stockées sont effacées et l'appareil est servi en clair ;
        présentes mais invalides, 400 ``invalid_push_keys`` plutôt qu'un
        abonnement qui ne recevrait jamais rien de lisible.

        La réponse dit ce que le serveur fera : ``webpush`` (clés stockées) et
        ``webpush_types``, les types de messages qu'il chiffrera désormais
        toujours pour cet appareil. L'app refuse un message de ces types qui
        n'arrive pas chiffré ; elle accepte les autres tels quels.
        """
        data = _body()
        endpoint = (data.get("endpoint") or "").strip()
        # The server POSTs to this endpoint on every inbound message
        # (push_transport._post), so an endpoint resolving to a private/loopback
        # address is a blind-SSRF sink. Reuse the module's anti-SSRF guard.
        if not endpoint.startswith(("http://", "https://")) \
                or not _safe_media_url(endpoint):
            return _json({"error": "invalid_endpoint"}, 400)
        try:
            p256dh, auth = parse_push_keys(data.get("p256dh"), data.get("auth"))
        except ValueError:
            return _json({"error": "invalid_push_keys"}, 400)
        if p256dh and not webpush_available():
            # Des clés valides, un serveur qui ne sait pas chiffrer : on le dit
            # plutôt que de promettre un chiffrement qui n'aura pas lieu.
            _logger.warning(
                "Mobile API : clés WebPush ignorées (appareil %s), http_ece ou "
                "cryptography absent de l'image.", device.id)
            p256dh = auth = False
        device.sudo().write({
            "push_endpoint": endpoint,
            "push_p256dh": p256dh,
            "push_auth": auth,
            "app_version": data.get("app_version") or device.app_version,
        })
        webpush = bool(p256dh and auth)
        return _json({
            "ok": True,
            "webpush": webpush,
            "webpush_types": (request.env["sms.archive.unifiedpush"]._webpush_types()
                              if webpush else []),
        })

    @http.route(f"{BASE}/register_fcm", type="http", auth="public", methods=["POST"],
                csrf=False, save_session=False)
    @_authed
    def register_fcm(self, device, **kw):
        data = _body()
        token = (data.get("fcm_token") or "").strip()
        if not token:
            return _json({"error": "missing_fcm_token"}, 400)
        device.sudo().write({
            "fcm_token": token,
            "app_version": data.get("app_version") or device.app_version,
        })
        return _json({"ok": True})
