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

from werkzeug.utils import redirect as wz_redirect

from odoo import fields, http
from odoo.exceptions import AccessDenied, UserError
from odoo.http import request

from .voipms_webhook import _safe_media_url

_logger = logging.getLogger(__name__)

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
        device.sudo().write({"last_seen": fields.Datetime.now()})
        request.update_env(user=device.user_id.id)
        try:
            return fn(self, device, *args, **kw)
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
        request.update_env(user=uid)
        return _json({
            "token": device.device_token,
            "user_id": uid,
            "user_name": user.name,
            "lines": request.env["sms.archive.thread"].get_lines(),
            "config": request.env["sms.archive.thread"].get_messenger_config(),
        })

    # ── Auth par connexion web (capture le login Odoo : mot de passe, SSO
    #    Authentik « les sessions SSO », MFA — tout ce que la page /web/login offre) ──
    @http.route(f"{BASE}/auth/start", type="http", auth="user", methods=["GET"],
                csrf=False)
    def auth_start(self, **kw):
        """Ouverte dans un onglet du navigateur. ``auth='user'`` → si non
        connecté, Odoo redirige vers /web/login (mot de passe OU boutons SSO),
        puis revient ici authentifié. On émet alors un CODE unique et on
        redirige vers le deep-link de l'app. Le vrai jeton n'apparaît jamais ici."""
        redirect = kw.get("redirect") or ""
        state = kw.get("state") or ""
        if not _allowed_redirect(redirect):
            return request.make_response(
                "Redirection non autorisée.", status=400,
                headers=[("Content-Type", "text/plain; charset=utf-8")])
        user = request.env.user
        if not user.has_group(SMS_USER_GROUP):
            return request.make_response(
                "Ce compte n'a pas accès à la messagerie SMS.", status=403,
                headers=[("Content-Type", "text/plain; charset=utf-8")])
        code = request.env["sms.archive.mobile.device"]._issue_pending(
            user.id, name=kw.get("device_name"))
        sep = "&" if "?" in redirect else "?"
        target = "%s%scode=%s&state=%s" % (
            redirect, sep, urllib.parse.quote(code), urllib.parse.quote(state))
        return wz_redirect(target, code=302)

    @http.route(f"{BASE}/auth/exchange", type="http", auth="public",
                methods=["POST"], csrf=False, save_session=False)
    def auth_exchange(self, **kw):
        """L'app échange le code unique (reçu par deep-link) contre le jeton
        porteur durable, sur HTTPS."""
        data = _body()
        device = request.env["sms.archive.mobile.device"]._exchange(
            (data.get("code") or "").strip())
        if not device:
            return _json({"error": "invalid_or_expired_code"}, 401)
        if data.get("fcm_token"):
            device.sudo().write({"fcm_token": data["fcm_token"].strip()})
        request.update_env(user=device.user_id.id)
        return _json({
            "token": device.device_token,
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
        device.sudo().write({"active": False, "fcm_token": False})
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
        """Enregistre l'endpoint UnifiedPush (ntfy) de l'app pour cet appareil."""
        data = _body()
        endpoint = (data.get("endpoint") or "").strip()
        # The server POSTs to this endpoint on every inbound message
        # (push_transport._post), so an endpoint resolving to a private/loopback
        # address is a blind-SSRF sink. Reuse the module's anti-SSRF guard.
        if not endpoint.startswith(("http://", "https://")) \
                or not _safe_media_url(endpoint):
            return _json({"error": "invalid_endpoint"}, 400)
        device.sudo().write({
            "push_endpoint": endpoint,
            "app_version": data.get("app_version") or device.app_version,
        })
        return _json({"ok": True})

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
