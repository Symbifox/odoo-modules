"""Push over UnifiedPush (ntfy) — native Android app, no Google dependency.

The app registers with a UnifiedPush distributor (the phone's ntfy app) and
gets an ``endpoint`` URL. The server POSTs JSON to it; ntfy relays to the
distributor, which hands it to the app, which draws its own notification
(deep link into the thread + quick reply).

The SMS module publishes to the very same endpoint with its own ntfy token —
one app registration, two independent publishers. Payloads are told apart by
``type``, so neither module needs to know the other exists:

  - {type:"mail", title, body, email_id, thread_key, account_id}
  - {type:"mail_clear", email_id}       → drop one notification
  - {type:"mail_clear_all"}             → drop every mail notification

Every send is defensive: a dead endpoint or an unreachable ntfy must never
break the IMAP sync cron that triggered it.

Encryption (C-M3, audit 2026-09-08): once the device has handed over its
WebPush subscription keys (``/register_push``), the body leaves encrypted per
RFC 8291 (``aes128gcm``). Without keys (app ≤ 2.41.0) the JSON goes out in the
clear, exactly as before. See ``push_request``.
"""
import base64
import binascii
import ipaddress
import json
import logging
import re
import socket
from urllib.parse import urlparse

import requests

from odoo import _, api, models

# ⚠️ Tolerant import. `http_ece` and `cryptography` are in every tenant image
# today, but this module does not declare them: an image that lost them must
# not take the whole registry down over a notification. Without them the server
# answers `webpush: false` and keeps pushing in the clear.
try:
    import http_ece
    from cryptography.hazmat.primitives.asymmetric import ec
except ImportError:  # pragma: no cover
    http_ece = ec = None

_logger = logging.getLogger(__name__)

# ⚠️ Un drapeau de configuration se lit TOLÉRANT, jamais par égalité exacte.
# `fields.Boolean(config_parameter=...)` sur `res.config.settings` stocke la
# chaîne « True » / « False », alors que les `set_param` posés à la main écrivent
# « 1 » / « 0 ». Comparer à « 1 » fait donc basculer le réglage au premier
# enregistrement du formulaire — sans erreur, sans trace, sans retour en arrière.
# Payé trois fois : rapport de sauvegarde muet (2026-06-30), Gen par courriel
# hors service (2026-09-03) et le réveil du softphone, mort huit jours par le
# MÊME clic que Gen (BF, 2026-09-10).
# ⚠️ `get_param` rend `False` — pas la valeur par défaut — quand la clé est
# ABSENTE : c'est pourquoi `False` compte ici comme « rien », et non comme « non ».
def _truthy(valeur, defaut=False):
    if valeur is None or valeur is False or valeur == "":
        return defaut
    return str(valeur).strip().lower() in ("1", "true", "vrai", "yes", "oui", "on", "t", "y")



NTFY_TOKEN_PARAM = "bf_email_management.ntfy_publish_token"
# The ntfy server is one per tenant; its address lives with the SMS half, the
# first module to have needed it. Read here only to gate the bearer token.
NTFY_BASE_PARAM = "bf_sms_archive.ntfy_base_url"
POST_TIMEOUT = 8


def ntfy_auth_allowed(endpoint, base_url):
    """True when ``endpoint`` is on the configured ntfy server's host.

    🔴 The publish token is a SERVER secret. Attached to whatever endpoint the
    device registered, it left for any public host the caller chose. It only
    goes to our ntfy. Pure: the bench drives it. Audit 2026-09-08 (S-M3).
    """
    try:
        e, b = urlparse(endpoint or ""), urlparse(base_url or "")
    except ValueError:
        return False
    return bool(e.hostname and b.hostname and e.hostname.lower() == b.hostname.lower())
# Longest push body worth sending: ntfy relays it as one message and the
# phone truncates in the shade anyway.
BODY_MAX = 180
# Past this many new messages in one sync, send a single summary instead.
BATCH_NOTIFY_MAX = 5


def _ip_is_public(ip):
    return not (
        ip.is_private or ip.is_loopback or ip.is_link_local
        or ip.is_multicast or ip.is_reserved or ip.is_unspecified
    )


def _host_is_public(host):
    """False when the host resolves to a private/loopback/reserved address.

    Anti-SSRF guard: the server POSTs to whatever endpoint the device
    registered, so an endpoint pointing at an internal service or the cloud
    metadata address (169.254.169.254) would be a blind-SSRF sink.
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError):
        return False
    if not infos:
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if not _ip_is_public(ip):
            return False
    return True


def safe_push_endpoint(url):
    """True when ``url`` is an http(s) URL resolving to a public address."""
    if not url or not isinstance(url, str):
        return False
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    return _host_is_public(parsed.hostname)


# ── WebPush (RFC 8291) ────────────────────────────────────────────────────────
# Until now the subject and preview of every email crossed ntfy in the clear
# (S-I3), and anyone who knew a device's endpoint could post a forged
# notification to it (C-M3). End-to-end encryption settles both: ntfy only sees
# an opaque blob, and the app drops what it cannot decrypt.
#
# Kept apart from bf_sms_archive's identical helper on purpose: the two modules
# install independently, and this one must not require the other.

# How long ntfy keeps a message the device has not collected yet.
WEBPUSH_TTL = 86400
# RFC 8188 record size: http_ece's default, and pywebpush's. Every message fits
# in ONE record; each record costs 17 bytes (delimiter and tag).
WEBPUSH_RS = 4096
# ⚠️ Not `WEBPUSH_RS - 17`: the app's decrypter (Tink `WebPushHybridDecrypt`,
# the one inside UnifiedPush connector 3.x) refuses a WHOLE message, header
# included, past 4096 bytes. That leaves 4096 - 86 - 17 = 3993 bytes of
# plaintext. Past it the push would leave, ntfy would relay it, and the app
# would drop it silently (found with app 2.42.0, 2026-09-14); better it fails
# here, in the server log.
WEBPUSH_MAX_PLAINTEXT = WEBPUSH_RS - 86 - 17

_B64URL_RE = re.compile(r"^[A-Za-z0-9_-]+={0,2}$")


def webpush_available():
    """True when this image can encrypt (``http_ece`` and ``cryptography``)."""
    return http_ece is not None and ec is not None


def _b64url_bytes(value):
    """Bytes of a base64url string (padding optional), or None.

    ⚠️ The alphabet is checked BEFORE decoding: ``urlsafe_b64decode`` silently
    drops characters it does not know, so a mangled key would pass for a
    different, valid-looking one.
    """
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or not _B64URL_RE.match(value):
        return None
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, binascii.Error):
        return None


def _b64url(raw):
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def parse_push_keys(p256dh, auth):
    """Subscription keys from the app → normalized ``(p256dh, auth)``.

    ``(False, False)`` when BOTH are absent: a pre-encryption app, still served
    in the clear. ``ValueError`` for anything else that is not a valid pair:
    one key alone, a key that does not decode, a point off the P-256 curve, an
    auth secret that is not 16 bytes. Pure: the bench drives it.

    ⚠️ The point is checked on the curve, not just by length: an invalid point
    would only raise at send time, inside the IMAP cron, far from the app that
    sent it and that would believe its notifications encrypted.
    """
    if not p256dh and not auth:
        return False, False
    key, secret = _b64url_bytes(p256dh), _b64url_bytes(auth)
    if key is None or secret is None:
        raise ValueError("unreadable subscription key")
    if len(key) != 65 or key[0] != 0x04:
        raise ValueError("p256dh must be an uncompressed P-256 point")
    if len(secret) != 16:
        raise ValueError("auth must be 16 bytes")
    if webpush_available():
        # Raises ValueError when the point is not on the curve. Without the
        # library the caller stores nothing anyway.
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), key)
    return _b64url(key), _b64url(secret)


def webpush_encrypt(plaintext, p256dh, auth, private_key=None, salt=None):
    """Encrypt ``plaintext`` (bytes) for the ``(p256dh, auth)`` subscription.

    RFC 8291 ``aes128gcm``: a FRESH ephemeral P-256 key per message, a random
    salt, a single record, and the ephemeral public key as the header
    ``keyid``, which is exactly what ``pywebpush.WebPusher.encode`` does.
    ``private_key`` and ``salt`` exist only so the bench can replay the RFC's
    Appendix A vector.
    """
    if not webpush_available():
        raise ValueError("WebPush encryption unavailable on this server")
    if len(plaintext) > WEBPUSH_MAX_PLAINTEXT:
        # A second record is valid RFC 8188, but ntfy and UnifiedPush cap a
        # message at 4 KB: it would never arrive.
        raise ValueError("message too long for a single record")
    key, secret = _b64url_bytes(p256dh), _b64url_bytes(auth)
    if key is None or secret is None:
        raise ValueError("unreadable subscription key")
    return http_ece.encrypt(
        plaintext,
        salt=salt,
        private_key=private_key or ec.generate_private_key(ec.SECP256R1()),
        dh=key,
        auth_secret=secret,
        rs=WEBPUSH_RS,
        version="aes128gcm",
    )


def push_request(payload, p256dh=None, auth=None, ttl=WEBPUSH_TTL):
    """``(body, headers)`` for one push, without ``Authorization``.

    With both keys: encrypted body, ``Content-Encoding: aes128gcm`` and
    ``TTL``. Without: the clear JSON and the headers of old, byte for byte, so
    a pre-encryption app sees no difference.
    """
    if p256dh and auth:
        plaintext = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        return webpush_encrypt(plaintext, p256dh, auth), {
            "Content-Type": "application/octet-stream",
            "Content-Encoding": "aes128gcm",
            "TTL": str(int(ttl)),
        }
    return json.dumps(payload), {"Content-Type": "application/json"}


class BfEmailUnifiedPush(models.AbstractModel):
    _name = "bf.email.unifiedpush"
    _description = "Envoi push UnifiedPush/ntfy (app native, sans Google)"

    @api.model
    def _devices(self, owner):
        if not owner:
            return self.env["bf.email.mobile.device"]
        # Interrupteur par locataire. Vider push_endpoint coupait bien la
        # poussée, mais l'app se réinscrit à son prochain lancement et tout
        # revient. Défaut « 1 » : aucun autre locataire ne change de
        # comportement au déploiement. Tâche BF.
        if not _truthy(self.env["ir.config_parameter"].sudo().get_param(
                "bf_email.push_enabled"), defaut=True):
            return self.env["bf.email.mobile.device"]
        return self.env["bf.email.mobile.device"].sudo().search([
            ("user_id", "=", owner.id),
            ("active", "=", True),
            ("push_endpoint", "!=", False),
        ])

    @api.model
    def _auth_headers(self, endpoint):
        icp = self.env["ir.config_parameter"].sudo()
        headers = {"Content-Type": "application/json"}
        token = icp.get_param(NTFY_TOKEN_PARAM)
        if token and ntfy_auth_allowed(endpoint, icp.get_param(NTFY_BASE_PARAM)):
            headers["Authorization"] = "Bearer %s" % token
        return headers

    @api.model
    def _webpush_types(self):
        """Message types THIS server always encrypts for a device that handed
        over its keys.

        ⚠️ The app rejects an undecrypted message of a listed type, so a type
        only belongs here once it really goes through ``_send``.
        """
        return ["mail", "mail_clear", "mail_clear_all"]

    @api.model
    def _post(self, endpoint, payload, p256dh=None, auth=None):
        body, headers = push_request(payload, p256dh, auth)
        # The ntfy token follows the same rule as before, encrypted or not.
        token = self._auth_headers(endpoint).get("Authorization")
        if token:
            headers["Authorization"] = token
        return requests.post(
            endpoint, data=body, headers=headers,
            timeout=POST_TIMEOUT,
            # The endpoint was vetted as public; a 30x would send this POST —
            # bearer token included — somewhere that never was. Redirects are
            # meaningless for a push publish anyway.
            allow_redirects=False,
        )

    @api.model
    def _send(self, owner, payload):
        """POST ``payload`` to every endpoint of ``owner``. Never raises.

        Dead endpoints (403/404/410) are purged so a reinstalled app doesn't
        leave the cron POSTing into the void forever.

        Encrypted for each device that carries both keys, clear for the others:
        one person's two phones may run two versions of the app.
        """
        for dev in self._devices(owner):
            # Re-checked at send time, not only at registration: DNS can be
            # repointed at an internal address after the endpoint was stored.
            if not safe_push_endpoint(dev.push_endpoint):
                _logger.warning(
                    "bf.email push: endpoint non public sur l'appareil %s, purgé.",
                    dev.id,
                )
                dev.write({"push_endpoint": False})
                continue
            try:
                # ⚠️ A device that handed over its keys NEVER gets clear text:
                # if encryption fails, `push_request` raises and the message is
                # lost, which the app would have done with it anyway.
                resp = self._post(dev.push_endpoint, payload,
                                  dev.push_p256dh, dev.push_auth)
                if resp.status_code in (403, 404, 410):
                    dev.write({"push_endpoint": False})
                    _logger.info(
                        "bf.email push: endpoint mort (appareil %s, HTTP %s) purgé.",
                        dev.id, resp.status_code,
                    )
                elif resp.status_code >= 400:
                    _logger.warning("bf.email push: HTTP %s — %s",
                                    resp.status_code, resp.text[:150])
            except Exception:  # noqa: BLE001
                _logger.warning("bf.email push: erreur d'envoi.", exc_info=True)

    @api.model
    def _notify_new_emails(self, emails):
        """Notify the owner of each freshly ingested inbound email.

        Grouped per owner so a batch IMAP pull costs one device lookup per
        user rather than one per message.
        """
        by_owner = {}
        for rec in emails:
            if rec.direction != "in" or rec.is_handled or not rec.user_id:
                continue
            by_owner.setdefault(rec.user_id, self.env["bf.email"])
            by_owner[rec.user_id] |= rec

        for owner, recs in by_owner.items():
            if not self._devices(owner):
                continue
            # A first sync (or a catch-up after downtime) pulls a whole
            # batch_size at once. Ringing the phone a hundred times is worse
            # than useless, so past the cap it gets one line instead.
            if len(recs) > BATCH_NOTIFY_MAX:
                self._send(owner, {
                    "type": "mail",
                    "title": _("%d nouveaux courriels") % len(recs),
                    "body": _("Synchronisation de la boîte de réception"),
                    "preview": "",
                    "email_id": False,
                    "thread_key": False,
                    "account_id": recs[0].account_id.id or False,
                })
                continue
            # Oldest first: the shade stacks by posting order, so the newest
            # message has to be posted LAST to end up on top.
            for rec in recs.sorted("date"):
                self._send(owner, {
                    "type": "mail",
                    "title": rec._push_sender_label(),
                    "body": (rec.subject or "(sans objet)")[:BODY_MAX],
                    "preview": (rec.body_preview or "")[:BODY_MAX],
                    "email_id": rec.id,
                    "thread_key": rec._mobile_thread_key(),
                    "account_id": rec.account_id.id or False,
                })

    @api.model
    def _notify_clear(self, owner, email_id):
        """Read/handled elsewhere — drop the notification on the phone."""
        if not owner:
            return
        self._send(owner, {"type": "mail_clear", "email_id": int(email_id)})

    @api.model
    def _notify_clear_all(self, owner):
        if not owner:
            return
        self._send(owner, {"type": "mail_clear_all"})
