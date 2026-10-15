# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
"""Lecture sûre d'un agenda publié (lot 6).

Une adresse d'abonnement est saisie par un usager : sans garde, Odoo irait
chercher ce qu'on lui donne, y compris une adresse interne (le serveur
lui-même, un conteneur voisin, les métadonnées de l'hébergeur). Même garde
que bf_bookmarks : HTTPS seulement, port 443, résolution unique et épinglée,
toute adresse non publique refusée, redirections revérifiées une à une, taille
et durée plafonnées.
"""

import ipaddress
import socket
import time
from urllib.parse import urljoin, urlsplit

try:
    import certifi
    import urllib3
except ImportError:  # pragma: no cover - dépendances d'Odoo
    certifi = urllib3 = None

CGNAT = ipaddress.ip_network("100.64.0.0/10")
MAX_OCTETS = 5 * 1024 * 1024
MAX_REDIRECTIONS = 3
# Durée totale d'une lecture, redirections comprises : un serveur qui distille un
# octet toutes les 19 s tenait sinon le fil de cron indéfiniment (relecture adverse, M3).
DUREE_MAX = 60
USER_AGENT = "Mozilla/5.0 (compatible; Symbifox-Agenda/1.0; +https://symbifox.com)"


class LectureRefusee(Exception):
    """Adresse ou réponse refusée par la garde."""


def ip_publique(ip):
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
                or ip.is_multicast or ip.is_unspecified
                or (ip.version == 4 and ip in CGNAT))


def ip_epinglee(hote, port, ips_interdites=()):
    try:
        infos = socket.getaddrinfo(hote, port, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError) as exc:
        raise LectureRefusee("adresse introuvable") from exc
    choisie = None
    for info in infos or []:
        ip = ipaddress.ip_address(info[4][0])
        if not ip_publique(ip):
            raise LectureRefusee("adresse non publique")
        if str(ip) in ips_interdites:
            raise LectureRefusee("adresse de ce serveur")
        choisie = choisie or str(ip)
    if not choisie:
        raise LectureRefusee("adresse introuvable")
    return choisie


def normaliser(url):
    """webcal:// et webcals:// sont du HTTPS ; tout autre schéma que https est refusé."""
    url = (url or "").strip()
    if url.lower().startswith(("webcal://", "webcals://")):
        url = "https://" + url.split("://", 1)[1]
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise LectureRefusee("seules les adresses https (ou webcal) sont acceptées")
    return url


def lire(url, etag=None, modifie=None, hotes_interdits=(), ips_interdites=()):
    """``(statut, en-têtes, corps)`` ; 304 rend un corps vide.

    ``hotes_interdits`` : les noms d'hôte refusés en plus des adresses non
    publiques (celui de l'Odoo lui-même : s'abonner à son propre lien de partage
    faisait croître l'agenda à chaque passage).
    """
    if urllib3 is None:
        raise LectureRefusee("urllib3 absent")
    url = normaliser(url)
    echeance = time.monotonic() + DUREE_MAX
    interdits = {h.lower() for h in hotes_interdits if h}
    for _saut in range(MAX_REDIRECTIONS + 1):
        parts = urlsplit(url)
        if parts.scheme != "https":
            raise LectureRefusee("redirection hors https")
        if (parts.port or 443) != 443:
            raise LectureRefusee("port non standard")
        hote = parts.hostname
        if hote and hote.lower() in interdits:
            raise LectureRefusee("adresse de ce serveur")
        ip = ip_epinglee(hote, 443, ips_interdites)
        chemin = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        # Pas de compression : la garde de taille porte alors sur ce qui arrive vraiment.
        entetes = {"Host": hote, "User-Agent": USER_AGENT, "Accept": "text/calendar, */*;q=0.5",
                   "Accept-Encoding": "identity"}
        if etag:
            entetes["If-None-Match"] = etag
        if modifie:
            entetes["If-Modified-Since"] = modifie
        pool = urllib3.HTTPSConnectionPool(
            ip, port=443, cert_reqs="CERT_REQUIRED", ca_certs=certifi.where(),
            assert_hostname=hote, server_hostname=hote,
            timeout=urllib3.Timeout(connect=5, read=20), retries=False)
        try:
            rep = pool.request("GET", chemin, headers=entetes, redirect=False, preload_content=False)
            try:
                if rep.status in (301, 302, 303, 307, 308):
                    destination = rep.headers.get("Location")
                    if not destination:
                        raise LectureRefusee("redirection sans destination")
                    url = normaliser(urljoin(url, destination))
                    continue
                if rep.status == 304:
                    return 304, dict(rep.headers), b""
                annonce = rep.headers.get("Content-Length")
                if annonce and annonce.isdigit() and int(annonce) > MAX_OCTETS:
                    raise LectureRefusee("agenda trop volumineux")
                corps = bytearray()
                for morceau in rep.stream(65536):
                    corps.extend(morceau)
                    if len(corps) > MAX_OCTETS:
                        raise LectureRefusee("agenda trop volumineux")
                    if time.monotonic() > echeance:
                        raise LectureRefusee("lecture trop lente")
                return rep.status, dict(rep.headers), bytes(corps)
            finally:
                rep.release_conn()
        finally:
            pool.close()
    raise LectureRefusee("trop de redirections")
