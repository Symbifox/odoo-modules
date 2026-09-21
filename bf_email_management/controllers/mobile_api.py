"""Mobile API for the email module — consumed by the Odoo Inbox Android app.

A plain REST/JSON contract under ``/bf_email_management/mobile/v1/``, not
Odoo's ``call_kw`` JSON-RPC: the app is a third party, and pinning it to the
ORM's wire format would make every model rename a breaking client change.

Authentication is bearer-token, and the token can only be obtained by
completing a real Odoo web login:

  1. ``GET  /auth/start``   — opened in a browser tab (``auth="user"``, so
     password / Authentik SSO / TOTP all apply), shows the consent page.
  2. ``POST /auth/consent`` — « Allow » redirects to the app's deep link with
     a single-use code.
  3. ``POST /auth/exchange`` — swaps the code for the durable bearer token.
  4. Every later call carries ``Authorization: Bearer <token>``.

There is no password route here — see bf_email_mobile_device.py for why.

Each authenticated call runs as the device's user (``request.update_env``),
so the per-owner ir.rules on bf.email apply unchanged; the model layer
additionally refuses rows owned by somebody else, which matters because
``group_email_admin`` can read every mailbox in the ORM.

The app also talks to ``bf_sms_archive``'s API on the same instance. The two
are deliberately independent — separate tokens, separate registrations — and
``GET /ping`` is how the app finds out whether this half is installed at all.
"""
import functools
import json
import logging
import urllib.parse

from markupsafe import Markup
from werkzeug.utils import redirect as wz_redirect

from odoo import fields, http
from odoo.exceptions import AccessError, UserError
from odoo.http import request
from odoo.service.model import PG_CONCURRENCY_EXCEPTIONS_TO_RETRY

from ..models.bf_email_mobile import TOO_LARGE, UPLOAD_SINGLE_MAX
from ..models.push_transport import (
    parse_push_keys, safe_push_endpoint, webpush_available,
)

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


BASE = "/bf_email_management/mobile/v1"
API_VERSION = 1
REDIRECT_SCHEMES_PARAM = "bf_email_management.mobile_redirect_schemes"
DEFAULT_REDIRECT_SCHEMES = "odooinbox://"
# Attachments are streamed straight out of raw_rfc822 / ir.attachment. Past
# this size the phone should not be the download path at all.
ATTACHMENT_MAX_BYTES = 25 * 1024 * 1024


def _json(data, status=200):
    return request.make_response(
        json.dumps(data, default=str),
        headers=[("Content-Type", "application/json; charset=utf-8")],
        status=status,
    )


def _body(**kw):
    """Parsed request body.

    The app posts JSON. A form-encoded POST is already drained into ``kw`` by
    the dispatcher before the handler runs, leaving the raw stream empty — so
    falling back to ``kw`` turns "wrong Content-Type" into a working request
    instead of a silently empty dict and a confusing "missing_parameters".
    """
    raw = request.httprequest.get_data(as_text=True)
    if not raw:
        return dict(kw)
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return dict(kw)
    return parsed if isinstance(parsed, dict) else dict(kw)


def _flag(value):
    return str(value).lower() in ("1", "true", "yes")


def _grouped(source):
    """Le repli en conversations, tel que l'app l'affiche en ce moment.

    Absent par défaut = replié, comme ``/threads`` : un client plus ancien qui
    n'envoie pas le drapeau garde le comportement contre lequel il a été
    écrit. Le booléen JSON ``false`` et la chaîne ``"0"`` disent la même chose,
    l'app n'ayant pas à savoir par quel encodage le paramètre voyage.
    """
    value = source.get("grouped", True)
    if isinstance(value, bool):
        return value
    return str(value).lower() not in ("0", "false", "no")


def _allowed_redirect(redirect):
    """True when the redirect target is one of this instance's app schemes.

    Without it ``/auth/start`` is an open redirect that hands a live exchange
    code to whatever URL the caller names.
    """
    schemes = (request.env["ir.config_parameter"].sudo().get_param(
        REDIRECT_SCHEMES_PARAM) or DEFAULT_REDIRECT_SCHEMES)
    allowed = tuple(s.strip() for s in schemes.split(",") if s.strip())
    return bool(redirect) and bool(allowed) and redirect.startswith(allowed)


# ── Pairing consent page (S-M1) ───────────────────────────────────────────────
# Duplicated in ``bf_sms_archive`` rather than shared: the two modules install
# independently of each other.
_CONSENT_HEADERS = [
    ("Content-Type", "text/html; charset=utf-8"),
    # Never inside a frame: a third-party page framing it transparently would
    # get « Allow » tapped blind (clickjacking).
    ("X-Frame-Options", "DENY"),
    # ⚠️ No `form-action`: Chrome also applies it to the REDIRECT that follows
    # the form submission, and the bounce to the app scheme would be blocked
    # without a word.
    ("Content-Security-Policy",
     "frame-ancestors 'none'; default-src 'none'; style-src 'unsafe-inline'"),
    # The page carries the session's CSRF token.
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


def _bounce(ask, **params):
    """Back to the app's deep link, ``state`` always attached.

    303 after the consent page's POST, so the browser does not resubmit the
    form.
    """
    redirect = ask["redirect"]
    sep = "&" if "?" in redirect else "?"
    query = urllib.parse.urlencode({**params, "state": ask["state"]})
    code = 303 if request.httprequest.method == "POST" else 302
    return wz_redirect(f"{redirect}{sep}{query}", code=code)


def _consent_page(ask):
    """The page telling the person what they are about to allow.

    No JavaScript, readable on a phone, in the user's language. Nothing secret
    on it: no token, no code. Every value that came from the URL is escaped
    (``Markup`` escapes what it interpolates).
    """
    env = request.env
    user = env.user
    hidden = Markup("").join(
        Markup('<input type="hidden" name="%s" value="%s"/>') % (name, ask[name])
        for name in ("redirect", "state", "code_challenge",
                     "code_challenge_method", "device_name"))
    device = Markup("")
    if ask["device_name"]:
        device = Markup("<dt>%s</dt><dd>%s</dd>") % (
            env._("Device"), ask["device_name"])
    page = Markup(
        '<!DOCTYPE html><html lang="%(lang)s"><head><meta charset="utf-8"/>'
        '<meta name="viewport" content="width=device-width, initial-scale=1"/>'
        '<meta name="robots" content="noindex"/>'
        "<title>Symbifox Mobile</title><style>%(style)s</style></head>"
        "<body><main><h1>%(title)s</h1>"
        "<dl><dt>%(l_account)s</dt><dd>%(name)s<br/><small>%(login)s</small></dd>"
        "%(device)s"
        "<dt>%(l_access)s</dt><dd>%(access)s</dd></dl>"
        "<p>%(notice)s</p>"
        '<form method="post" action="%(action)s">'
        '<input type="hidden" name="csrf_token" value="%(csrf)s"/>%(hidden)s'
        '<button class="yes" type="submit" name="decision" value="allow">%(allow)s</button>'
        '<button class="no" type="submit" name="decision" value="deny">%(deny)s</button>'
        "</form></main></body></html>"
    ) % {
        "lang": (env.lang or "en_US").split("_")[0],
        "style": _CONSENT_STYLE,
        "title": env._("Symbifox Mobile wants to access your account"),
        "l_account": env._("Account"),
        "name": user.name or "",
        "login": user.login or "",
        "device": device,
        "l_access": env._("Access requested"),
        "access": env._("Your email"),
        "notice": env._("Only allow this if you just started signing in from the "
                        "app on your phone."),
        "action": f"{BASE}/auth/consent",
        "csrf": request.csrf_token(),
        "hidden": hidden,
        "allow": env._("Allow"),
        "deny": env._("Deny"),
    }
    return request.make_response(page, headers=_CONSENT_HEADERS)


def _authed(fn):
    """Resolve the bearer token, switch the env to its user, or 401."""
    @functools.wraps(fn)
    def wrapper(self, *args, **kw):
        header = request.httprequest.headers.get("Authorization", "")
        token = header[7:].strip() if header.startswith("Bearer ") else None
        device = request.env["bf.email.mobile.device"]._resolve(token)
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
            # « unexpected error » changeait un conflit d'une milliseconde en
            # 500 définitif, et l'app annulait le geste.
            raise
        # 🔴 Un refus ANNULE la transaction. Rendre une réponse ici
        # est un retour normal pour Odoo, qui valide alors ce qui a été écrit
        # avant l'erreur : le jeton anti-doublon de l'envoi (réservé avant les
        # gardes), les fiches contact créées pour les destinataires, les pièces
        # jointes déjà rattachées. Le téléphone, qui rejouait avec le même
        # jeton après avoir corrigé, recevait « doublon » et affichait « envoyé »
        # alors que rien n'était parti.
        except (UserError, AccessError) as exc:
            request.env.cr.rollback()
            return _json({"error": str(exc)}, 400)
        except (TypeError, ValueError) as exc:
            # Almost always a malformed parameter (a string where an int was
            # expected, a bad thread key). That is the caller's mistake, so it
            # gets a 400 — a 500 would tell the app to retry forever.
            request.env.cr.rollback()
            _logger.info("Mobile mail API: bad request — %s", exc)
            return _json({"error": "bad_request"}, 400)
        except Exception:  # noqa: BLE001
            request.env.cr.rollback()
            _logger.exception("Mobile mail API: unexpected error")
            return _json({"error": "server_error"}, 500)
    return wrapper


class BfEmailMobileApi(http.Controller):

    # ── Discovery ─────────────────────────────────────────────────────
    @http.route(f"{BASE}/ping", type="http", auth="public", methods=["GET"],
                csrf=False, save_session=False)
    def ping(self, **kw):
        """Capability probe. The app hits this on both module APIs to decide
        which tabs to show, before anyone has logged in."""
        module = request.env["ir.module.module"].sudo().search(
            [("name", "=", "bf_email_management")], limit=1)
        return _json({
            "ok": True,
            "module": "bf_email_management",
            # Le délai de péremption locale : voir `_peremption_locale`.
            "wipe_after_days": _peremption_locale(request.env),
            "api": API_VERSION,
            "version": module.installed_version or "",
            # Public on purpose: the app themes its instance and login screens
            # before anyone has signed in. Colours and a company name are not
            # a disclosure — the domain already says whose server this is.
            "branding": request.env["bf.email"].sudo()._mobile_branding(),
        })

    # ── Auth (web-login capture) ──────────────────────────────────────
    #
    # 🔴 Two steps since the audit of 2026-09-08 (S-M1). The GET used to issue
    # the pairing code and bounce straight to the app scheme: a third-party app
    # on the phone declaring that scheme could open the URL in the browser where
    # the person is already signed in and pair a device without them seeing
    # anything. PKCE cannot help, since that very app made the challenge. The
    # GET now shows what is being asked; only an « Allow » POST, CSRF token
    # included, issues the code.
    @http.route(f"{BASE}/auth/start", type="http", auth="user", methods=["GET"],
                csrf=False)
    def auth_start(self, **kw):
        """Validate the pairing request and render the consent page.

        No code is issued here. Failures come back through the deep link as
        ``?error=…`` rather than as an HTML page. The app chains this against
        both module APIs in one browser session; a dead-end error page on the
        first leg would strand the whole login instead of just disabling one
        tab.
        """
        error, ask = self._pairing_request(kw)
        if error is not None:
            return error
        return _consent_page(ask)

    @http.route(f"{BASE}/auth/consent", type="http", auth="user",
                methods=["POST"], csrf=True)
    def auth_consent(self, **kw):
        """The consent page's answer. ``csrf=True``: without the session's
        token, Odoo refuses the POST before calling us.

        ⚠️ Everything is RE-VALIDATED: hidden fields come from the browser, so
        from anyone. The user comes from the session, never from the form.
        """
        error, ask = self._pairing_request(kw)
        if error is not None:
            return error
        if kw.get("decision") != "allow":
            _logger.info("Mobile mail API: pairing denied by %s on the consent "
                         "page", request.env.user.login)
            return _bounce(ask, error="access_denied")
        code = request.env["bf.email.mobile.device"]._issue_pending(
            request.env.user.id, name=ask["device_name"],
            challenge=ask["code_challenge"])
        return _bounce(ask, code=code)

    def _pairing_request(self, kw):
        """Validate a pairing request: ``(error response, None)`` or
        ``(None, request)``. ONE definition for the GET and the POST, or the
        consent page would end up accepting what the start page refuses."""
        redirect = kw.get("redirect") or ""
        if not _allowed_redirect(redirect):
            return request.make_response(
                "Redirection non autorisée.", status=400,
                headers=[("Content-Type", "text/plain; charset=utf-8")]), None
        ask = {
            "redirect": redirect,
            "state": kw.get("state") or "",
            "code_challenge": (kw.get("code_challenge") or "").strip(),
            "code_challenge_method": (kw.get("code_challenge_method") or "S256").upper(),
            "device_name": (kw.get("device_name") or "").strip()[:80],
        }

        user = request.env.user
        # No dedicated group on this module: bf.email is owner-scoped and open
        # to every internal user. What actually decides whether the app is
        # useful is owning a mailbox.
        if not user.has_group("base.group_user"):
            return _bounce(ask, error="no_access"), None
        has_account = request.env["bf.email.account"].sudo().search_count([
            ("user_id", "=", user.id), ("active", "=", True)])
        if not has_account:
            return _bounce(ask, error="no_mailbox"), None

        # PKCE is mandatory. A custom app scheme is not exclusive on Android:
        # without a challenge, a code intercepted by another app would be
        # traded for a bearer token on the person's mailbox.
        #
        # No grace period, deliberately: devices already paired
        # keep their token and never come back through the exchange. Only a NEW
        # login started from a pre-lot APK breaks, and it breaks loudly, here,
        # with a readable reason.
        if not ask["code_challenge"] or ask["code_challenge_method"] != "S256":
            _logger.info(
                "Mobile mail API: pairing refused, PKCE challenge missing or "
                "method %s not accepted", ask["code_challenge_method"])
            return _bounce(ask, error="pkce_required"), None
        return None, ask

    @http.route(f"{BASE}/auth/exchange", type="http", auth="public",
                methods=["POST"], csrf=False, save_session=False)
    def auth_exchange(self, **kw):
        data = _body(**kw)
        device = request.env["bf.email.mobile.device"]._exchange(
            (data.get("code") or "").strip(),
            (data.get("code_verifier") or "").strip())
        if not device:
            return _json({"error": "invalid_or_expired_code"}, 401)
        # Handed out once, then sealed: only the hash stays in the database.
        jeton = device.sudo().device_token
        device._seal()
        request.update_env(user=device.user_id.id)
        return _json({
            "token": jeton,
            "user_id": device.user_id.id,
            "config": request.env["bf.email"].get_mobile_config(),
        })

    @http.route(f"{BASE}/logout", type="http", auth="public", methods=["POST"],
                csrf=False, save_session=False)
    @_authed
    def logout(self, device, **kw):
        device.sudo().write({"active": False, "push_endpoint": False})
        return _json({"ok": True})

    # ── Bootstrap ─────────────────────────────────────────────────────
    @http.route(f"{BASE}/config", type="http", auth="public", methods=["GET"],
                csrf=False, save_session=False)
    @_authed
    def config(self, device, **kw):
        return _json(request.env["bf.email"].get_mobile_config())

    # ── Reading ───────────────────────────────────────────────────────
    @http.route(f"{BASE}/threads", type="http", auth="public", methods=["GET"],
                csrf=False, save_session=False)
    @_authed
    def threads(self, device, **kw):
        return _json(request.env["bf.email"].get_mobile_threads(
            filter_name=kw.get("filter") or "inbox",
            search=kw.get("search") or None,
            account_id=int(kw["account_id"]) if kw.get("account_id") else None,
            offset=int(kw.get("offset") or 0),
            limit=int(kw.get("limit") or 25),
            # Default on: folding is what makes this a mail app rather than a
            # message log, so an old client that never sends the flag keeps
            # the behaviour it was written against.
            grouped=kw.get("grouped", "1") not in ("0", "false", "False"),
        ))

    @http.route(f"{BASE}/conversation", type="http", auth="public",
                methods=["GET"], csrf=False, save_session=False)
    @_authed
    def conversation(self, device, **kw):
        if not kw.get("thread_key"):
            return _json({"error": "missing_thread_key"}, 400)
        return _json(request.env["bf.email"].get_mobile_conversation(
            kw["thread_key"], load_images=_flag(kw.get("load_images"))))

    @http.route(f"{BASE}/message", type="http", auth="public", methods=["GET"],
                csrf=False, save_session=False)
    @_authed
    def message(self, device, **kw):
        if not kw.get("id"):
            return _json({"error": "missing_id"}, 400)
        return _json(request.env["bf.email"].get_mobile_message(
            int(kw["id"]), load_images=_flag(kw.get("load_images"))))

    @http.route(f"{BASE}/attachment", type="http", auth="public",
                methods=["GET"], csrf=False, save_session=False)
    @_authed
    def attachment(self, device, **kw):
        """Stream one attachment.

        Indexed by position in the message's own attachment list, never by
        ir.attachment id: the id space is global, and a device that could name
        an arbitrary one would be reading the whole filestore.
        """
        if not kw.get("email_id") or kw.get("idx") is None:
            return _json({"error": "missing_parameters"}, 400)
        record = request.env["bf.email"].browse(int(kw["email_id"])).exists()
        if not record or record.user_id.id != request.env.uid:
            return _json({"error": "not_found"}, 404)
        found = record._mobile_attachment_bytes(
            int(kw["idx"]), max_bytes=ATTACHMENT_MAX_BYTES)
        if found is TOO_LARGE:
            return _json({"error": "too_large"}, 413)
        if not found:
            return _json({"error": "not_found"}, 404)
        name, mimetype, payload = found
        return request.make_response(payload, headers=[
            ("Content-Type", mimetype),
            ("Content-Length", str(len(payload))),
            ("Content-Disposition",
             "attachment; filename*=UTF-8''%s" % urllib.parse.quote(name)),
            # Attachment bytes must not sit in a shared proxy cache.
            ("Cache-Control", "private, max-age=0, no-store"),
        ])

    # ── Compteurs ─────────────────────────────────────────────────────
    @http.route(f"{BASE}/counts", type="http", auth="public", methods=["GET"],
                csrf=False, save_session=False)
    @_authed
    def counts(self, device, **kw):
        """Les seuls totaux, relus à part de la liste.

        ⚠️ Cette route existe parce que l'app n'avait AUCUN moyen de rafraîchir
        ses pastilles : elles ne descendaient qu'à l'ouverture de l'écran et
        dans la réponse d'une mutation faite depuis le téléphone. Un courriel
        qui arrivait, un ménage fait au navigateur, ou simplement OUVRIR un fil
        — qui marque lu côté serveur — laissaient « Non lus · 5 » au-dessus
        d'une liste qui n'avait plus rien à lire. Tirer pour rafraîchir n'y
        changeait rien. Répondre les totaux sans la page de courriels rend le
        rafraîchissement assez léger pour être fait à chaque relecture.
        """
        return _json({"counts": request.env["bf.email"]._mobile_counts(
            grouped=_grouped(kw))})

    # ── Triage ────────────────────────────────────────────────────────
    @http.route(f"{BASE}/mark_read", type="http", auth="public",
                methods=["POST"], csrf=False, save_session=False)
    @_authed
    def mark_read(self, device, **kw):
        data = _body(**kw)
        counts = request.env["bf.email"].mobile_mark_read(
            data.get("email_ids"), grouped=_grouped(data))
        return _json({"ok": True, "counts": counts})

    @http.route(f"{BASE}/handle", type="http", auth="public", methods=["POST"],
                csrf=False, save_session=False)
    @_authed
    def handle(self, device, **kw):
        data = _body(**kw)
        counts = request.env["bf.email"].mobile_set_handled(
            data.get("email_ids"), handled=bool(data.get("handled", True)),
            grouped=_grouped(data))
        return _json({"ok": True, "counts": counts})

    @http.route(f"{BASE}/snooze", type="http", auth="public", methods=["POST"],
                csrf=False, save_session=False)
    @_authed
    def snooze(self, device, **kw):
        data = _body(**kw)
        counts = request.env["bf.email"].mobile_snooze(
            data.get("email_ids"), data.get("until_ms"),
            grouped=_grouped(data))
        return _json({"ok": True, "counts": counts})

    @http.route(f"{BASE}/attachment/upload", type="http", auth="public",
                methods=["POST"], csrf=False, save_session=False)
    @_authed
    def attachment_upload(self, device, **kw):
        """Stage one outbound file, returning the id a send can reference.

        Multipart rather than base64-in-JSON: phone attachments are photos and
        PDFs, and base64 would inflate every one of them by a third over a
        mobile link.

        The returned id is only usable by this device, for this user, once —
        see ``_mobile_claim_uploads``.
        """
        upload = request.httprequest.files.get("file")
        if upload is None:
            return _json({"error": "missing_file"}, 400)
        content = upload.read(UPLOAD_SINGLE_MAX + 1)
        if not content:
            return _json({"error": "empty_file"}, 400)
        if len(content) > UPLOAD_SINGLE_MAX:
            return _json({"error": "too_large"}, 413)
        return _json(request.env["bf.email"].mobile_stage_upload(
            device,
            filename=upload.filename,
            content=content,
            mimetype=upload.mimetype,
        ))

    # ── Sending ───────────────────────────────────────────────────────
    @http.route(f"{BASE}/reply", type="http", auth="public", methods=["POST"],
                csrf=False, save_session=False)
    @_authed
    def reply(self, device, **kw):
        data = _body(**kw)
        if not data.get("email_id"):
            return _json({"error": "missing_email_id"}, 400)
        record = request.env["bf.email"].browse(int(data["email_id"])).exists()
        if not record or record.user_id.id != request.env.uid:
            return _json({"error": "not_found"}, 404)
        return _json(record.mobile_reply(
            mode=data.get("mode") or "reply",
            body=data.get("body") or "",
            to=data.get("to"),
            cc=data.get("cc"),
            device=device,
            attachment_ids=data.get("attachment_ids"),
            client_token=data.get("client_token"),
            body_is_html=bool(data.get("body_is_html")),
            bcc=data.get("bcc"),
            subject=data.get("subject"),
            identity_id=data.get("identity_id"),
            scheduled_ms=data.get("scheduled_ms"),
        ))

    @http.route(f"{BASE}/reply/prepare", type="http", auth="public",
                methods=["GET"], csrf=False, save_session=False)
    @_authed
    def reply_prepare(self, device, **kw):
        """Ce qu'une réponse enverrait : destinataires, objet, adresse.

        En lecture seule, et sans créer de fiche contact : ouvrir un composeur
        n'est pas envoyer.
        """
        try:
            email_id = int(kw.get("email_id") or 0)
        except (TypeError, ValueError):
            email_id = 0
        if not email_id:
            return _json({"error": "missing_email_id"}, 400)
        record = request.env["bf.email"].browse(email_id).exists()
        if not record or record.user_id.id != request.env.uid:
            return _json({"error": "not_found"}, 404)
        return _json(record.mobile_reply_prepare(mode=kw.get("mode") or "reply"))

    @http.route(f"{BASE}/compose", type="http", auth="public", methods=["POST"],
                csrf=False, save_session=False)
    @_authed
    def compose(self, device, **kw):
        data = _body(**kw)
        return _json(request.env["bf.email"].mobile_compose(
            to=data.get("to"),
            subject=data.get("subject"),
            body=data.get("body") or "",
            cc=data.get("cc"),
            res_model=data.get("res_model"),
            res_id=data.get("res_id"),
            device=device,
            attachment_ids=data.get("attachment_ids"),
            client_token=data.get("client_token"),
            body_is_html=bool(data.get("body_is_html")),
            bcc=data.get("bcc"),
            identity_id=data.get("identity_id"),
            scheduled_ms=data.get("scheduled_ms"),
        ))

    # ── Envois programmés ────────────────────────────────────
    @http.route(f"{BASE}/scheduled", type="http", auth="public",
                methods=["GET"], csrf=False, save_session=False)
    @_authed
    def scheduled(self, device, **kw):
        return _json(request.env["bf.email"].mobile_scheduled(
            offset=int(kw.get("offset") or 0),
            limit=int(kw.get("limit") or 25),
        ))

    @http.route(f"{BASE}/scheduled/unschedule", type="http", auth="public",
                methods=["POST"], csrf=False, save_session=False)
    @_authed
    def scheduled_unschedule(self, device, **kw):
        data = _body(**kw)
        return _json(request.env["bf.email"].mobile_unschedule(data.get("id")))

    # ── Brouillons du poste ──────────────────────────────────
    # Le téléphone garde les siens dans un fichier local ; ces routes servent
    # l'AUTRE pile, celle qu'« Enregistrer comme brouillon » pose au poste.
    # Seuls les vrais brouillons remontent : un envoi différé part de lui-même
    # à sa date, et une note interne ne sort jamais par courriel.

    @http.route(f"{BASE}/drafts", type="http", auth="public", methods=["GET"],
                csrf=False, save_session=False)
    @_authed
    def drafts(self, device, **kw):
        return _json(request.env["bf.email"].mobile_drafts(
            offset=int(kw.get("offset") or 0),
            limit=int(kw.get("limit") or 25),
            search=kw.get("search"),
        ))

    @http.route(f"{BASE}/draft", type="http", auth="public", methods=["GET"],
                csrf=False, save_session=False)
    @_authed
    def draft(self, device, **kw):
        return _json(request.env["bf.email"].mobile_draft(kw.get("id")))

    @http.route(f"{BASE}/draft/save", type="http", auth="public",
                methods=["POST"], csrf=False, save_session=False)
    @_authed
    def draft_save(self, device, **kw):
        """Réécrire un brouillon du poste.

        ⚠️ Écriture PARTIELLE : une clé absente du corps de la requête n'est
        pas touchée. ``data.get`` rendrait ``None`` aussi bien pour « efface
        l'objet » que pour « je ne parle pas de l'objet » ; le test
        d'appartenance distingue les deux, et c'est ce qui permet à l'app de
        n'envoyer que ce que la personne a modifié.
        """
        data = _body(**kw)
        result = request.env["bf.email"].mobile_draft_save(
            draft_id=data.get("id"),
            device=device,
            base_version=data.get("version"),
            subject=data.get("subject") if "subject" in data else None,
            body=data.get("body") if "body" in data else None,
            body_is_html=bool(data.get("body_is_html")),
            to=data.get("to") if "to" in data else None,
            attachment_ids=(data.get("attachment_ids")
                            if "attachment_ids" in data else None),
        )
        # 200 et non 409, comme le doublon d'envoi juste au-dessus : le client
        # lit l'issue dans la charge utile, et `ApiClient` ne remonte d'un
        # statut d'erreur que la clé `error`. Un 409 ferait donc perdre à
        # l'app la version du serveur qu'on prend soin de lui rendre.
        return _json(result)

    @http.route(f"{BASE}/draft/send", type="http", auth="public",
                methods=["POST"], csrf=False, save_session=False)
    @_authed
    def draft_send(self, device, **kw):
        data = _body(**kw)
        return _json(request.env["bf.email"].mobile_draft_send(
            draft_id=data.get("id"), base_version=data.get("version")))

    @http.route(f"{BASE}/draft/delete", type="http", auth="public",
                methods=["POST"], csrf=False, save_session=False)
    @_authed
    def draft_delete(self, device, **kw):
        data = _body(**kw)
        return _json(request.env["bf.email"].mobile_draft_delete(
            draft_id=data.get("id")))

    # ── Odoo-side actions ─────────────────────────────────────────────
    @http.route(f"{BASE}/contacts", type="http", auth="public", methods=["GET"],
                csrf=False, save_session=False)
    @_authed
    def contacts(self, device, **kw):
        """Address-book completion for the composer's To/Cc fields.

        ``groups=1`` ajoute les groupes de destinataires, chacun portant ses
        membres dépliés. Le drapeau est explicite pour que le
        client 2.37, qui attend une adresse par entrée, ne change pas de
        comportement sans mise à jour.
        """
        return _json(request.env["bf.email"].mobile_search_contacts(
            kw.get("q") or "", limit=int(kw.get("limit") or 20),
            include_groups=str(kw.get("groups") or "") in ("1", "true", "True")))

    @http.route(f"{BASE}/records", type="http", auth="public", methods=["GET"],
                csrf=False, save_session=False)
    @_authed
    def records(self, device, **kw):
        return _json(request.env["bf.email"].mobile_search_records(
            kw.get("model") or "", kw.get("q") or "",
            limit=int(kw.get("limit") or 20)))

    @http.route(f"{BASE}/route", type="http", auth="public", methods=["POST"],
                csrf=False, save_session=False)
    @_authed
    def route(self, device, **kw):
        data = _body(**kw)
        if not (data.get("email_id") and data.get("res_model")
                and data.get("res_id")):
            return _json({"error": "missing_parameters"}, 400)
        record = request.env["bf.email"].browse(int(data["email_id"])).exists()
        if not record or record.user_id.id != request.env.uid:
            return _json({"error": "not_found"}, 404)
        return _json(record.mobile_route(data["res_model"], int(data["res_id"])))

    @http.route(f"{BASE}/spawn", type="http", auth="public", methods=["POST"],
                csrf=False, save_session=False)
    @_authed
    def spawn(self, device, **kw):
        data = _body(**kw)
        if not (data.get("email_id") and data.get("kind")):
            return _json({"error": "missing_parameters"}, 400)
        record = request.env["bf.email"].browse(int(data["email_id"])).exists()
        if not record or record.user_id.id != request.env.uid:
            return _json({"error": "not_found"}, 404)
        return _json(record.mobile_spawn(data["kind"]))

    # ── Push ──────────────────────────────────────────────────────────
    @http.route(f"{BASE}/register_push", type="http", auth="public",
                methods=["POST"], csrf=False, save_session=False)
    @_authed
    def register_push(self, device, **kw):
        """Store the device's UnifiedPush endpoint.

        The server POSTs to this URL on every inbound message, so an endpoint
        resolving to a private address would make the cron a blind-SSRF sink.

        Body: ``{endpoint, app_version, p256dh, auth}``. The two WebPush
        subscription keys are OPTIONAL: absent (app ≤ 2.41.0), any stored keys
        are cleared and the device is served in the clear; present but invalid,
        400 ``invalid_push_keys`` rather than a subscription that would never
        receive anything readable.

        The answer says what the server will do: ``webpush`` (keys stored) and
        ``webpush_types``, the message types it will from now on always
        encrypt for this device. The app rejects an unencrypted message of
        those types and accepts the others as they come.
        """
        data = _body(**kw)
        endpoint = (data.get("endpoint") or "").strip()
        if not safe_push_endpoint(endpoint):
            return _json({"error": "invalid_endpoint"}, 400)
        try:
            p256dh, auth = parse_push_keys(data.get("p256dh"), data.get("auth"))
        except ValueError:
            return _json({"error": "invalid_push_keys"}, 400)
        if p256dh and not webpush_available():
            # Valid keys, a server that cannot encrypt: say so rather than
            # promise an encryption that will not happen.
            _logger.warning(
                "Mobile mail API: WebPush keys ignored (device %s), http_ece or "
                "cryptography missing from the image.", device.id)
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
            "webpush_types": (request.env["bf.email.unifiedpush"]._webpush_types()
                              if webpush else []),
        })
