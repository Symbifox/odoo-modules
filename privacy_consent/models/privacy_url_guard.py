"""SSRF guard helpers for outbound HTTP calls (DocuSeal, LibreSign)."""
import ipaddress
import socket
from urllib.parse import urlparse

from odoo.exceptions import UserError


def assert_safe_url(url, label):
    """Reject non-http(s) schemes and resolved addresses that hit
    loopback, link-local, or cloud metadata endpoints (169.254.169.254).

    Private RFC1918 ranges (10/8, 172.16/12, 192.168/16) are allowed since
    self-hosted Nextcloud/DocuSeal commonly run on internal networks.
    """
    if not url:
        raise UserError(f"L'URL {label} n'est pas configurée.")

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise UserError(
            f"L'URL {label} doit utiliser http ou https (reçu : {parsed.scheme})."
        )
    host = parsed.hostname
    if not host:
        raise UserError(f"L'URL {label} est invalide (hôte manquant).")

    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        # DNS failure surfaces downstream as a proper ConnectionError.
        return

    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified:
            raise UserError(
                f"L'URL {label} pointe vers une adresse interdite ({addr})."
            )
