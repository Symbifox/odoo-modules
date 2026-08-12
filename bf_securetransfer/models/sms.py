"""SMS delivery layer for bf_securetransfer — VoIP.ms ``sendSMS`` REST.

Module-level functions (not a model), same shape as ``s3.py``: a thin,
testable wrapper around the VoIP.ms API used to deliver the recipient OTP
by text message when a secure send elects the SMS channel.

Credentials NEVER live in the database (a DB dump — e.g. a staging refresh —
would carry the prod credentials): the API user/password come from the
environment (``BF_SECURETRANSFER_VOIPMS_USER`` / ``_PASSWORD``) or from
``odoo.conf`` (``bf_securetransfer_voipms_user`` / ``..._password``). The
non-secret settings — the sending DID and the on/off switch — live in
``ir.config_parameter`` under the ``bf_securetransfer.`` prefix and are shown
in the Settings panel.

VoIP.ms SMS is North-American (NANP) only: destinations are normalized to a
bare 10-digit number and anything else is refused (the caller then falls back
to e-mail so a recipient is never locked out).
"""
import json
import logging
import os
import re
import urllib.parse
import urllib.request

from odoo.tools import config as odoo_config

_logger = logging.getLogger(__name__)

_API_URL = "https://voip.ms/api/v1/rest.php"
_TIMEOUT = 20  # seconds — an OTP must not hang a request thread
_TRUE_VALUES = ("1", "true", "yes", "on")
# VoIP.ms caps a single SMS at 160 UTF-8 BYTES, not 160 characters: 160 is
# accepted, 161 is refused with ``sms_toolong``. Every accented character costs
# two bytes in UTF-8, so a French body can be well under 160 characters and
# still be refused — which is how the ``sms_toolong`` failures in the send log
# happened. Measure bytes, never len().
_MAX_SMS_BYTES = 160
# The VoIP.ms REST endpoint sits behind a WAF that answers 403 to urllib's
# default User-Agent ("Python-urllib/x.y"). Any other UA is accepted; without
# one, every call fails and send() silently falls back to e-mail.
_USER_AGENT = "bf_securetransfer/1.0 (+https://bluefoxconsultant.com)"


def _param(env, key, default=None):
    """Read one ``bf_securetransfer.``-prefixed ir.config_parameter."""
    return env["ir.config_parameter"].sudo().get_param(
        "bf_securetransfer." + key, default
    )


def enabled(env):
    """Whether the operator has switched the SMS channel on."""
    return (_param(env, "sms_enabled", "0") or "").strip().lower() in _TRUE_VALUES


def _credentials():
    """(api_username, api_password) from the environment or odoo.conf, never
    the database. Returns ('', '') when unset."""
    user = (os.environ.get("BF_SECURETRANSFER_VOIPMS_USER")
            or odoo_config.get("bf_securetransfer_voipms_user") or "").strip()
    password = (os.environ.get("BF_SECURETRANSFER_VOIPMS_PASSWORD")
                or odoo_config.get("bf_securetransfer_voipms_password") or "").strip()
    return user, password


def has_credentials():
    """True when both the API user and password are declared out-of-band."""
    user, password = _credentials()
    return bool(user and password)


def did(env):
    """The configured sending DID, normalized to 10 digits (or '' if unset /
    invalid)."""
    return normalize_na(_param(env, "sms_did", "") or "") or ""


def configured(env):
    """Fully wired for sending: switched on, credentials present, DID valid."""
    return enabled(env) and has_credentials() and bool(did(env))


def truncate_utf8(text, max_bytes=_MAX_SMS_BYTES):
    """``text`` cut to at most ``max_bytes`` UTF-8 bytes, never mid-character.

    VoIP.ms measures the body in bytes, so slicing on characters (``text[:160]``)
    lets an accented French body through at up to 320 bytes and the API answers
    ``sms_toolong``. Cutting on a raw byte slice would be worse still: it can
    split a multi-byte character and produce a body that no longer decodes, so
    the tail byte is dropped rather than mangled.
    """
    raw = (text or "").encode("utf-8")
    if len(raw) <= max_bytes:
        return text or ""
    return raw[:max_bytes].decode("utf-8", "ignore")


def normalize_na(number):
    """A bare 10-digit NANP number, or None. Accepts the usual human formats
    (+1 (514) 555-1234, 1-514-555-1234, 5145551234). A leading country code 1
    is dropped; anything that is not exactly 10 digits afterward is refused."""
    digits = re.sub(r"\D", "", number or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits if len(digits) == 10 else None


def send(env, dst, message):
    """Deliver ``message`` to ``dst`` via VoIP.ms. Returns True on a confirmed
    send, False otherwise (not configured, bad number, API/network error) —
    it NEVER raises, so an OTP caller can cleanly fall back to e-mail.

    The message and both numbers are kept out of the logs (PII / phone); only
    the transfer-side caller logs a redacted trace in the access journal.
    """
    if not configured(env):
        _logger.info("bf_securetransfer SMS: non configuré — envoi ignoré.")
        return False
    to = normalize_na(dst)
    from_did = did(env)
    if not to or not from_did:
        _logger.warning("bf_securetransfer SMS: numéro destinataire invalide.")
        return False
    user, password = _credentials()
    body = truncate_utf8(message)
    if body != (message or ""):
        # Truncation is never harmless: an OTP or a link cut in half reaches the
        # recipient as noise. Log the sizes (no PII) so it is visible.
        _logger.warning(
            "bf_securetransfer SMS: corps tronqué à %s octets (était %s) — "
            "le message a été raccourci avant l'envoi.",
            _MAX_SMS_BYTES, len((message or "").encode("utf-8")),
        )
    params = {
        "api_username": user,
        "api_password": password,
        "method": "sendSMS",
        "did": from_did,
        "dst": to,
        "message": body,
    }
    url = _API_URL + "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as resp:
            body = resp.read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001 — network/HTTP: log type, fall back
        _logger.warning("bf_securetransfer SMS: échec réseau VoIP.ms (%s).",
                         type(exc).__name__)
        return False
    try:
        data = json.loads(body)
    except ValueError:
        _logger.warning("bf_securetransfer SMS: réponse VoIP.ms illisible.")
        return False
    status = (data.get("status") or "").strip()
    if status != "success":
        # status carries VoIP.ms error codes (invalid_credentials,
        # sms_toll_free_not_allowed, ...) — safe to log, no PII.
        _logger.warning("bf_securetransfer SMS: VoIP.ms a refusé (%s).", status)
        return False
    return True
