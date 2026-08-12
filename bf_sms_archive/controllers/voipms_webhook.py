"""Webhook entrant VOIP.ms (SMS URL Callback).

VOIP.ms exécute un GET sur cette URL à chaque message entrant, avec les variables
``{TO}`` (notre DID), ``{FROM}`` (expéditeur), ``{MESSAGE}``, ``{ID}``, ``{TIMESTAMP}``
et ``{MEDIA}`` (MMS). Aucune signature n'est fournie par VOIP.ms → on authentifie
via un ``token`` secret porté par la ligne (``sms.archive.line.webhook_token``).

L'endpoint DOIT répondre le texte brut ``ok`` ; sinon VOIP.ms ré-essaie aux 30 min.
"""
import base64
import ipaddress
import logging
import socket
from urllib.parse import urlparse

import certifi
import urllib3

from odoo import http
from odoo.http import Response, request

from odoo.addons.bf_sms_archive.models.voipms_time import voipms_date_to_ms

_logger = logging.getLogger(__name__)

_MEDIA_TIMEOUT = 15
_MEDIA_CONNECT_TIMEOUT = 5
_MEDIA_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
_MAX_MEDIA_TOKENS = 3


def _text(body, status=200):
    return Response(body, status=status, content_type="text/plain; charset=utf-8")


def _ip_is_public(ip):
    """False for private/loopback/link-local/reserved/multicast/unspecified."""
    return not (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified)


def _host_is_public(host):
    """False si l'hôte résout vers une adresse privée/loopback/link-local/réservée.

    Garde-fou anti-SSRF : empêche le téléchargement de média de pointer vers des
    services internes ou l'endpoint de métadonnées cloud (169.254.169.254, etc.).
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError):
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if not _ip_is_public(ip):
            return False
    return True


def _safe_media_url(url):
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    return _host_is_public(parsed.hostname)


def _resolve_pinned_ip(host, port):
    """Resolve ``host`` ONCE and return a single public IP to pin the socket to.

    Raises ValueError on resolution failure or if ANY resolved address is
    non-public (so a rebinder mixing a public and a private A record is rejected
    too). Pinning the connection to this exact IP is what closes the DNS-rebind /
    TOCTOU gap: otherwise ``_safe_media_url`` and the actual fetch resolve the
    name twice and can see different answers. Prefers IPv4 to keep the pinned
    connection pool simple.
    """
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError) as exc:
        raise ValueError("résolution DNS impossible") from exc
    if not infos:
        raise ValueError("aucune adresse résolue")
    ipv4, ipv6 = None, None
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError as exc:
            raise ValueError("adresse résolue invalide") from exc
        if not _ip_is_public(ip):
            raise ValueError("l'hôte résout vers une adresse non publique")
        if ip.version == 4 and ipv4 is None:
            ipv4 = addr
        elif ip.version == 6 and ipv6 is None:
            ipv6 = addr
    return ipv4 or ipv6


def _download_media(url, idx):
    """Télécharge une URL média avec garde-fous.

    Anti-SSRF / anti-DNS-rebind : on résout l'hôte UNE fois, on valide que TOUTES
    les adresses sont publiques, puis on épingle la connexion sur cette IP (le nom
    d'hôte d'origine reste présenté pour le SNI + la vérification du certificat
    TLS). Taille plafonnée, streamée, sans suivre les redirections.
    """
    parsed = urlparse(url)
    host = parsed.hostname
    if parsed.scheme not in ("http", "https") or not host:
        raise ValueError("URL média invalide")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    pinned_ip = _resolve_pinned_ip(host, port)
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query
    timeout = urllib3.Timeout(connect=_MEDIA_CONNECT_TIMEOUT, read=_MEDIA_TIMEOUT)
    if parsed.scheme == "https":
        pool = urllib3.HTTPSConnectionPool(
            pinned_ip, port=port, cert_reqs="CERT_REQUIRED",
            ca_certs=certifi.where(), assert_hostname=host,
            server_hostname=host, timeout=timeout, retries=False,
        )
    else:
        pool = urllib3.HTTPConnectionPool(
            pinned_ip, port=port, timeout=timeout, retries=False,
        )
    try:
        resp = pool.request(
            "GET", path, headers={"Host": host},
            redirect=False, preload_content=False, decode_content=False,
        )
        try:
            if resp.status != 200:
                raise ValueError("statut HTTP %s" % resp.status)
            declared = resp.headers.get("Content-Length")
            if declared and int(declared) > _MEDIA_MAX_BYTES:
                raise ValueError("média trop volumineux")
            buf = bytearray()
            for chunk in resp.stream(8192, decode_content=False):
                buf.extend(chunk)
                if len(buf) > _MEDIA_MAX_BYTES:
                    raise ValueError("média trop volumineux")
            ct = resp.headers.get(
                "Content-Type", "application/octet-stream").split(";")[0]
        finally:
            resp.release_conn()
    finally:
        pool.close()
    filename = url.rstrip("/").split("/")[-1].split("?")[0] or f"media-{idx}"
    return {
        "content_type": ct,
        "filename": filename,
        "data_b64": base64.b64encode(bytes(buf)).decode(),
    }


def _fetch_media(raw_media):
    """Transforme le champ {MEDIA} en parts MMS. Tolérant aux pannes.

    Le format exact de {MEDIA} (URL vs base64) varie. On télécharge uniquement les
    URL http(s) vers des hôtes PUBLICS (anti-SSRF), taille plafonnée et sans suivre
    les redirections. Toute autre valeur (ou un téléchargement raté) est conservée
    en clair pour ne rien perdre. Ne lève jamais : un média raté n'empêche pas
    l'ingestion du message texte.
    """
    parts = []
    if not raw_media:
        return parts
    tokens = [t.strip() for t in raw_media.replace(",", " ").split() if t.strip()]
    for idx, token in enumerate(tokens[:_MAX_MEDIA_TOKENS]):
        is_url = token.lower().startswith(("http://", "https://"))
        if is_url and _safe_media_url(token):
            try:
                parts.append(_download_media(token, idx))
                continue
            except Exception:
                _logger.warning("MMS média non téléchargé : %s", token[:120], exc_info=True)
        elif is_url:
            _logger.warning("MMS média refusé (hôte non public) : %s", token[:120])
        parts.append({
            "content_type": "text/plain",
            "filename": f"media-{idx}.txt",
            "text": token,
        })
    return parts


class VoipmsSmsWebhook(http.Controller):

    @http.route(
        "/bf_sms_archive/api/voipms/sms",
        type="http",
        auth="public",
        methods=["GET", "POST"],
        csrf=False,
        save_session=False,
    )
    def inbound_sms(self, token=None, **params):
        # 1. Authentification par jeton de ligne
        if not token:
            return _text("missing token", status=400)
        Line = request.env["sms.archive.line"].sudo()
        line = Line.search([("webhook_token", "=", token), ("active", "=", True)], limit=1)
        if not line:
            _logger.warning("Webhook VOIP.ms : jeton inconnu")
            return _text("forbidden", status=403)

        # 2. Champs VOIP.ms (insensible à la casse des clés)
        lower = {k.lower(): v for k, v in params.items()}
        sender = lower.get("from") or ""
        message = lower.get("message") or ""
        voipms_id = lower.get("id") or ""
        raw_date = lower.get("date") or lower.get("timestamp") or ""
        raw_media = lower.get("media") or ""
        if not sender:
            return _text("missing from", status=400)

        # 3. Ingestion (déduplication par voipms_id en priorité)
        try:
            parts = _fetch_media(raw_media)
            rec, created = request.env["sms.archive.message"].sudo()._ingest_one(
                phone_raw=sender,
                owner_id=line.owner_id.id,
                direction="in",
                body=message,
                date_ms=voipms_date_to_ms(raw_date, env=request.env),
                is_mms=bool(parts),
                parts=parts or None,
                batch_id="voipms-webhook",
                voipms_id=voipms_id or None,
                line_id=line.id,
                is_read=False,
            )
            if created:
                rec._notify_bus(kind="new")
        except Exception:
            _logger.exception("Webhook VOIP.ms : échec d'ingestion")
            # On répond quand même 200/ok : un retry ne corrigera pas une erreur applicative.
            return _text("ok")

        return _text("ok")
