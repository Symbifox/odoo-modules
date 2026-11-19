"""Device authorization flow for Blue Fox OS desktop widgets (RFC 8628).

  1. ``POST /auth/device/start``  — public. The widget asks for a pairing.
     Answers ``device_secret`` (its own, never shown) and
     ``verification_uri_complete`` (the page to open in a browser).
  2. ``GET  /auth/device/approve`` — ``auth="user"``, so Odoo demands the
     session: password, Authentik SSO and TOTP all apply. Shows the consent
     page.
  3. ``POST /auth/device/consent`` — CSRF-protected « Allow » / « Deny ».
  4. ``POST /auth/device/poll``   — public, ``device_code`` + ``device_secret``.
     Answers ``authorization_pending`` until approval, then the bearer token,
     exactly once.

🔴 A ``type="http"`` route that returns a normal response is COMMITTED by
Odoo, even when that response says « error ». Every handler that writes rolls
back explicitly before answering an error, otherwise a refused step would
leave its first half in the database.
"""
import json
import logging
import re

from markupsafe import Markup

from odoo import fields, http
from odoo.http import request

from ..models.bf_desktop_device import (
    APPS,
    PAIRING_TTL_SECONDS,
    POLL_INTERVAL_SECONDS,
)

_logger = logging.getLogger(__name__)

BASE = "/bf_desktop_pairing/v1"

#: Longest JSON body accepted. Nothing here carries content.
MAX_BODY = 8 * 1024


def _json(data, status=200):
    return request.make_response(
        json.dumps(data, default=str),
        headers=[
            ("Content-Type", "application/json; charset=utf-8"),
            ("Cache-Control", "no-store"),
        ],
        status=status,
    )


#: Les couleurs de marque, dans l'ordre de repli de la maison. ⚠️ Ce module ne
#: dépend que de `base` et `web` : AUCUN de ces champs n'existe forcément, et
#: `bf_onboarding_base` (qui déclare les deux premiers) peut ne pas être
#: installé. D'où la lecture par `_fields`, et jamais par attribut.
BRAND_ACCENT_FIELDS = ("report_brand_primary", "report_brand_color",
                       "bf_brand_color", "primary_color")
BRAND_DARK_FIELDS = ("report_brand_dark", "secondary_color")

#: Six chiffres hexadécimaux précédés d'un dièse, et rien d'autre.
_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")


def _hex_or_none(valeur):
    """Une couleur, ou rien.

    ⚠️ Une valeur saisie en base n'est pas une donnée de confiance : ces champs
    sont des `Char` libres, remplis à la main locataire par locataire. Ce qui
    sort d'ici est peint par le widget, donc il doit être d'une forme connue —
    sinon une valeur sale traverse jusqu'au poste de la personne.
    """
    v = (valeur or "").strip()
    return v if _HEX.match(v) else None


def _first_brand(company, champs):
    for nom in champs:
        if nom in company._fields:
            couleur = _hex_or_none(company[nom])
            if couleur:
                return couleur
    return None


def _body():
    """Le corps JSON, ou None.

    ⚠️ Le type de contenu est EXIGÉ. Sans cela, un POST en ``text/plain`` est
    une « requête simple » au sens CORS : n'importe quelle page web piégée
    pouvait faire ouvrir une demande d'appairage depuis l'adresse de chacun de
    ses visiteurs, sans préflight et sans que le navigateur demande rien. Le
    frein par appelant ne protégeait alors plus rien.

    ⚠️ ``get_data()`` sans ``cache=False`` : le corps doit rester relisible, car
    Odoo REJOUE la requête entière sur un conflit d'écriture, et un corps vidé
    au premier passage rendrait un refus au second.
    """
    type_contenu = (request.httprequest.content_type or "").split(";")[0].strip()
    if type_contenu != "application/json":
        return None
    data = request.httprequest.content_length or 0
    if data > MAX_BODY:
        return None
    data = request.httprequest.get_data() or b""
    if len(data) > MAX_BODY:
        return None
    if not data.strip():
        return {}
    try:
        parsed = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _base_url():
    return request.env["ir.config_parameter"].sudo().get_param(
        "web.base.url") or request.httprequest.host_url.rstrip("/")


# ── Consent page ─────────────────────────────────────────────────────────────
# Written here rather than as a QWeb template on purpose: it must render with
# no asset bundle, no JavaScript and no module data, so that it still works on
# a freshly installed tenant and on a Live USB session.
_CONSENT_HEADERS = [
    ("Content-Type", "text/html; charset=utf-8"),
    # Never inside a frame: a third-party page framing it transparently would
    # get « Allow » clicked blind (clickjacking).
    ("X-Frame-Options", "DENY"),
    # ⚠️ No `form-action`: it also applies to the redirect that follows the
    # POST in Chrome, which would be blocked without a word.
    ("Content-Security-Policy",
     "frame-ancestors 'none'; default-src 'none'; style-src 'unsafe-inline'"),
    ("Cache-Control", "no-store"),
    ("Referrer-Policy", "no-referrer"),
]

_CONSENT_STYLE = Markup(
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
    ".yes{background:#1f2328;color:#fff;border:1px solid #1f2328}"
    ".no{background:#fff;color:#1f2328;border:1px solid #8c959f}"
)


def _plain_page(title, message):
    """A dead end: expired, unknown or refused. No form, nothing to retry."""
    page = Markup(
        '<!DOCTYPE html><html lang="%(lang)s"><head><meta charset="utf-8"/>'
        '<meta name="viewport" content="width=device-width, initial-scale=1"/>'
        '<meta name="robots" content="noindex"/>'
        "<title>%(title)s</title><style>%(style)s</style></head>"
        "<body><main><h1>%(title)s</h1><p>%(message)s</p></main></body></html>"
    ) % {
        "lang": (request.env.lang or "en_US").split("_")[0],
        "style": _CONSENT_STYLE,
        "title": title,
        "message": message,
    }
    return request.make_response(page, headers=_CONSENT_HEADERS)


def _consent_page(device, pairing_code):
    """The page telling the person what they are about to allow.

    No JavaScript, readable on a small screen, in the user's language. Nothing
    secret on it beyond the pairing code it came with, and no bearer token.
    """
    env = request.env
    user = env.user
    # 🔴 Les trois nombres : un seul est celui que le widget affiche. C'est ce
    # qui permet enfin à la personne de reconnaître SON appairage — la page ne
    # pouvait rien lui donner à comparer, et le nom du poste vient du demandeur.
    choices = Markup("").join(
        Markup('<button class="yes" type="submit" name="decision" '
               'value="%s">%s</button>') % (nombre, "%02d" % nombre)
        for nombre in request.env["bf.desktop.device"]._match_choices(pairing_code)
    )
    page = Markup(
        '<!DOCTYPE html><html lang="%(lang)s"><head><meta charset="utf-8"/>'
        '<meta name="viewport" content="width=device-width, initial-scale=1"/>'
        '<meta name="robots" content="noindex"/>'
        "<title>%(app)s</title><style>%(style)s</style></head>"
        "<body><main><h1>%(title)s</h1>"
        "<dl><dt>%(l_account)s</dt><dd>%(name)s<br/><small>%(login)s</small></dd>"
        "<dt>%(l_device)s</dt><dd><small>%(device)s</small></dd>"
        "<dt>%(l_access)s</dt><dd>%(access)s</dd></dl>"
        "<p>%(notice)s</p>"
        '<form method="post" action="%(action)s">'
        '<input type="hidden" name="csrf_token" value="%(csrf)s"/>'
        '<input type="hidden" name="code" value="%(code)s"/>'
        "%(choices)s"
        '<button class="no" type="submit" name="decision" value="deny">%(deny)s</button>'
        "</form></main></body></html>"
    ) % {
        "lang": (env.lang or "en_US").split("_")[0],
        "style": _CONSENT_STYLE,
        "app": APPS.get(device.app, device.app),
        "title": env._("%s wants to open your notes") % APPS.get(device.app, device.app),
        "l_account": env._("Account"),
        "name": user.name or "",
        "login": user.login or "",
        "l_device": env._("Name the widget gave itself"),
        "device": device.name or "",
        "l_access": env._("Access requested"),
        "access": env._("Your notepad"),
        "notice": env._(
            "Pick the number shown by the widget on your desktop. If you did "
            "not just start this pairing, close this page: somebody else is "
            "asking to read your notes."),
        "action": f"{BASE}/auth/device/consent",
        "csrf": request.csrf_token(),
        "code": pairing_code,
        "choices": choices,
        "deny": env._("I did not start this"),
    }
    return request.make_response(page, headers=_CONSENT_HEADERS)


class BfDesktopPairing(http.Controller):

    @http.route(f"{BASE}/ping", type="http", auth="public", methods=["GET"],
                csrf=False, save_session=False)
    def ping(self, **kw):
        """Announces the flow, so a widget can tell an old tenant from a new one.

        Rend aussi QUI répond, sous quelle adresse, et à quelles couleurs.

        🔴 Pourquoi l'adresse canonique est ici : le widget doit parler à
        l'adresse que cette instance ANNONCE (`web.base.url`), pas seulement à
        celle qu'on a tapée. C'est elle que la page de consentement ouvrira
        dans le navigateur. Un locataire joignable par un domaine convivial
        distinct de son canonique faisait donc casser l'appairage au milieu,
        sans que rien ne puisse le dire — vu à l'essai, où
        `web.base.url` pointait la production.

        Le NOM est une garde, pas une décoration : il s'affiche avant qu'on
        approuve, donc on voit à qui on s'apparie. ⚠️ Rien de plus que ce qui
        est déjà public (le nom de la société et ses couleurs sont sur son site
        web, et l'adresse canonique sort de n'importe quelle redirection) : une
        route publique ne doit pas devenir une fenêtre sur l'annuaire.
        """
        module = request.env["ir.module.module"].sudo().search(
            [("name", "=", "bf_desktop_pairing")], limit=1)
        company = request.env.company.sudo()
        canonique = request.env["ir.config_parameter"].sudo().get_param(
            "web.base.url") or ""
        return _json({
            "ok": True,
            "module": "bf_desktop_pairing",
            "api": 1,
            # 🔴 `latest_version` est la colonne de la BASE, `installed_version`
            # celle du manifeste sur disque : les noms sont inversés dans Odoo.
            "version": module.latest_version or "",
            "apps": sorted(APPS),
            "flow": "device_code",
            "tenant": company.name or "",
            # Sans barre oblique finale : le widget compare cette chaîne à ce
            # qu'il a normalisé, et `normalizeServer` retire la barre.
            "canonical_url": canonique.rstrip("/"),
            "brand": {
                "accent": _first_brand(company, BRAND_ACCENT_FIELDS),
                "dark": _first_brand(company, BRAND_DARK_FIELDS),
            },
        })

    @http.route(f"{BASE}/auth/device/start", type="http", auth="public",
                methods=["POST"], csrf=False, save_session=False)
    def start(self, **kw):
        """Open a pairing request: ``{"app": "stickynotes", "name": "…"}``."""
        corps = _body()
        if corps is None:
            return _json({"error": "bad_request"}, 400)
        app = (corps.get("app") or "").strip()
        if app not in APPS:
            return _json({"error": "unknown_app"}, 400)
        Device = request.env["bf.desktop.device"]
        # 🔴 This route is public and it CREATES a row. Without a brake, anyone
        # who can reach the instance fills the table. The caller's address is
        # kept only as a fingerprint, only while the request is in flight, and
        # only to count: see ``_throttle_pairings``.
        empreinte = Device._fingerprint(request.httprequest.remote_addr)
        if not Device._throttle_pairings(empreinte):
            request.env.cr.rollback()
            return _json({"error": "slow_down",
                          "interval": POLL_INTERVAL_SECONDS}, 429)
        device, pairing_code, device_secret = Device._start_pairing(
            app, name=corps.get("name"), version=corps.get("version"),
            ip_hash=empreinte)
        if not device:
            request.env.cr.rollback()
            return _json({"error": "unknown_app"}, 400)
        uri = f"{_base_url()}{BASE}/auth/device/approve"
        return _json({
            "ok": True,
            # Le widget garde les deux. Ni l'un ni l'autre n'est affiché.
            "device_code": pairing_code,
            "device_secret": device_secret,
            # ⚠️ Aucun code à TAPER, par choix : voir le modèle.
            "verification_uri_complete": f"{uri}?code={pairing_code}",
            # Le nombre à AFFICHER, que la personne retrouvera parmi trois sur
            # la page. C'est ce qui lui permet de reconnaître son appairage.
            "match_number": "%02d" % Device._match_number(pairing_code),
            "interval": POLL_INTERVAL_SECONDS,
            "expires_in": PAIRING_TTL_SECONDS,
        }, 201)

    @http.route(f"{BASE}/auth/device/approve", type="http", auth="user",
                methods=["GET"], csrf=False)
    def approve(self, code=None, **kw):
        """The consent page. ``auth="user"`` is what demands the real login."""
        device = request.env["bf.desktop.device"]._pending(code)
        if not device:
            # Committed on purpose: ``_pending`` drops an expired request.
            return _plain_page(
                request.env._("Pairing expired"),
                request.env._("Start the pairing again from the widget."))
        return _consent_page(device, code)

    @http.route(f"{BASE}/auth/device/consent", type="http", auth="user",
                methods=["POST"], csrf=True)
    def consent(self, code=None, decision=None, **kw):
        Device = request.env["bf.desktop.device"]
        device = Device._pending(code)
        if not device:
            return _plain_page(
                request.env._("Pairing expired"),
                request.env._("Start the pairing again from the widget."))
        # 🔴 Un compte de portail n'a pas de bureau. `auth="user"` l'admet, et
        # `_resolve` rendait bien son jeton inerte — mais il créait quand même
        # une ligne appariée à son nom. Le refus appartient à l'approbation.
        if request.env.user.share:
            device._refuse()
            return _plain_page(
                request.env._("Pairing refused"),
                request.env._("This account cannot pair a desktop widget."))
        if decision == "deny" or decision is None:
            device._refuse()
            return _plain_page(
                request.env._("Pairing refused"),
                request.env._("Nothing was given access."))
        # La concordance : le nombre choisi est-il celui que le widget affiche ?
        #
        # ⚠️ Un mauvais choix DÉTRUIT la demande. Laisser réessayer rendrait les
        # trois nombres inutiles : qui a reçu le lien d'un tiers finirait par
        # tomber sur le bon.
        try:
            choisi = int(decision)
        except (TypeError, ValueError):
            device._refuse()
            return _plain_page(
                request.env._("Pairing refused"),
                request.env._("Nothing was given access."))
        if choisi != Device._match_number(code):
            device._refuse()
            _logger.warning(
                "Desktop pairing: wrong match number, request dropped.")
            return _plain_page(
                request.env._("That is not the number on the widget"),
                request.env._(
                    "The pairing was cancelled. If you did not start it on "
                    "this computer, somebody else was asking to read your "
                    "notes. Start again from the widget if it was you."))
        device._approve(request.env.uid)
        return _plain_page(
            request.env._("Widget paired"),
            request.env._("You can close this page and go back to your desktop."))

    @http.route(f"{BASE}/auth/device/poll", type="http", auth="public",
                methods=["POST"], csrf=False, save_session=False)
    def poll(self, **kw):
        """``{"device_code": …, "device_secret": …}`` → pending, or the token."""
        corps = _body()
        if corps is None:
            return _json({"error": "bad_request"}, 400)
        Device = request.env["bf.desktop.device"]
        device = Device._pending(corps.get("device_code"))
        if not device:
            # No rollback: ``_pending`` may have dropped an expired row, and
            # that drop is wanted. Odoo commits the normal response.
            return _json({"error": "expired_token"}, 400)
        # Rate limit: a widget that ignores ``interval`` is told to slow down
        # rather than served. Without it, a tight loop would hammer the row.
        now = fields.Datetime.now()
        if device.last_poll and (now - device.last_poll).total_seconds() \
                < POLL_INTERVAL_SECONDS:
            return _json({"error": "slow_down",
                          "interval": POLL_INTERVAL_SECONDS}, 429)
        device.sudo().write({"last_poll": now})
        if not device.approved:
            return _json({"error": "authorization_pending",
                          "interval": POLL_INTERVAL_SECONDS}, 202)
        token = device._deliver(corps.get("device_secret"))
        if not token:
            # Wrong secret: ``_deliver`` dropped the request. Keep that.
            return _json({"error": "access_denied"}, 403)
        return _json({
            "ok": True,
            "token": token,
            "token_type": "Bearer",
            "user": device.user_id.name or "",
            "login": device.user_id.login or "",
            "device_id": device.id,
        })
