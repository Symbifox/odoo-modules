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
"""
import ipaddress
import json
import logging
import socket
from urllib.parse import urlparse

import requests

from odoo import _, api, models

_logger = logging.getLogger(__name__)

NTFY_TOKEN_PARAM = "bf_email_management.ntfy_publish_token"
POST_TIMEOUT = 8
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
        # comportement au déploiement.
        if self.env["ir.config_parameter"].sudo().get_param(
                "bf_email.push_enabled", "1") != "1":
            return self.env["bf.email.mobile.device"]
        return self.env["bf.email.mobile.device"].sudo().search([
            ("user_id", "=", owner.id),
            ("active", "=", True),
            ("push_endpoint", "!=", False),
        ])

    @api.model
    def _post(self, endpoint, payload):
        token = self.env["ir.config_parameter"].sudo().get_param(NTFY_TOKEN_PARAM)
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer %s" % token
        return requests.post(
            endpoint, data=json.dumps(payload), headers=headers,
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
                resp = self._post(dev.push_endpoint, payload)
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
