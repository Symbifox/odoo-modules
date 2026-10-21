"""Garde réseau des connexions de données.

Une connexion fait sortir une requête DEPUIS le serveur Odoo, vers une adresse qu'un
administrateur du locataire a tapée. Sans garde, cet administrateur lirait le réseau
interne de l'hébergeur (autres conteneurs, interface d'administration du mandataire,
métadonnées infonuagiques) dans un tableau de bord. Donc :

- toute adresse qui n'est pas publique est refusée (bouclage, privée, CGNAT, lien local,
  multidiffusion, réservée), en IPv4 comme en IPv6, IPv4 encapsulée comprise, ainsi que
  les préfixes IPv6 qui transportent une adresse IPv4 (NAT64, 6to4, Teredo) ;
- la vérification se fait AU MOMENT de la connexion, sur l'adresse même qu'on joint :
  pas de bascule DNS possible entre la vérification et l'appel, et chaque redirection,
  qui ouvre une nouvelle connexion, repasse la garde ;
- la configuration du SERVEUR (odoo.conf), jamais la base, puisqu'un administrateur du
  locataire écrit dans ir.config_parameter, peut permettre des hôtes internes
  (`bf_bi_allowed_private_hosts`) ou en refuser de publics (`bf_bi_denied_hosts`, par
  exemple les adresses publiques de l'hébergeur lui-même) ;
- un échange entier tient dans TOTAL_TIMEOUT : à l'échéance, les sockets ouverts pour lui
  sont coupés, même si le serveur distant envoie un octet de temps en temps.
"""
import collections
import ipaddress
import os
import socket
import threading
import time

import requests
from requests.adapters import HTTPAdapter
from urllib3.connection import HTTPConnection, HTTPSConnection
from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool
from urllib3.exceptions import ConnectTimeoutError, NewConnectionError
from urllib3.exceptions import LocationParseError
from urllib3.util import connection as urllib3_connection
from urllib3.util import parse_url

from odoo.tools import config

TIMEOUT = 15
TOTAL_TIMEOUT = 30  # secondes pour tout l'échange : un serveur qui dribble ne retient pas un travailleur
MAX_BYTES = 20 * 1024 * 1024  # réponse lue au plus : la mémoire du processus est bornée
MAX_REDIRECTS = 5
# Préfixes IPv6 qui portent une adresse IPv4 : un traducteur sur le chemin mènerait à elle.
_EMBEDDING = [ipaddress.ip_network(n) for n in ("64:ff9b::/96", "64:ff9b:1::/48", "2002::/16", "2001::/32", "::/96")]
_local = threading.local()


class BlockedAddress(Exception):
    """L'hôte visé n'est pas une adresse publique, et le serveur ne le permet pas."""


class TooLarge(Exception):
    """La réponse dépasse le plafond d'octets."""


def _entries(option):
    raw = config.get(option) or ""
    return [e.strip().lower() for e in str(raw).split(",") if e.strip()]


def _networks(entries):
    networks = []
    for entry in entries:
        try:
            networks.append(ipaddress.ip_network(entry, strict=False))
        except ValueError:
            continue  # un nom d'hôte, comparé à part
    return networks


def _in(ip, networks):
    return any(ip.version == net.version and ip in net for net in networks)


def _is_public(ip):
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if ip.version == 6 and _in(ip, _EMBEDDING):
        return False
    return ip.is_global and not ip.is_multicast


def resolve(host, port):
    """Adresses IP de `host` (point de patch des essais)."""
    infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    return [info[4][0] for info in infos]


def vet(host, port):
    """Adresse IP à joindre pour `host`, si la garde la permet. Sinon BlockedAddress.

    Toutes les adresses du nom doivent passer : un nom qui en rend une publique et une
    privée est refusé, sinon le système choisirait peut-être la privée.
    """
    host = (host or "").strip("[]").rstrip(".").lower()
    if not host:
        raise BlockedAddress(host)
    try:
        addresses = resolve(host, port)
    except (socket.gaierror, UnicodeError):
        raise socket.gaierror("unresolvable") from None
    if not addresses:
        raise socket.gaierror("unresolvable")
    denied = _entries("bf_bi_denied_hosts")
    allowed = _entries("bf_bi_allowed_private_hosts")
    ips = [ipaddress.ip_address(a.split("%")[0]) for a in addresses]
    if host in denied or any(_in(ip, _networks(denied)) for ip in ips):
        raise BlockedAddress(host)
    if host in allowed:
        return addresses[0]
    networks = _networks(allowed)
    for ip in ips:
        if not _is_public(ip) and not _in(ip, networks):
            raise BlockedAddress(host)
    return addresses[0]


def cut_after(seconds, sockets):
    """Minuteur qui coupe (shutdown) les sockets de `sockets` à l'échéance.

    Couper débloque une lecture en attente dans un autre fil, ce que ne fait pas close().
    Rend le minuteur, à annuler (cancel) quand l'échange finit à temps.
    """
    def couper():
        for sock in list(sockets):
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
    timer = threading.Timer(seconds, couper)
    timer.daemon = True
    timer.start()
    return timer


# ----------------------------------------------------------------------
# HTTP : la garde branchée dans la connexion elle-même
# ----------------------------------------------------------------------

class _GuardedMixin:
    def _new_conn(self):
        # Même logique que urllib3, mais sur l'adresse vérifiée. Le nom reste celui de
        # l'hôte (self.host) : l'indication de nom TLS et la vérification du certificat
        # se font contre lui, pas contre l'adresse.
        try:
            address = vet(self.host, self.port)
        except socket.gaierror as e:
            raise NewConnectionError(self, "Name resolution failed") from e
        try:
            sock = urllib3_connection.create_connection(
                (address, self.port), self.timeout,
                source_address=self.source_address, socket_options=self.socket_options)
        except socket.timeout as e:
            raise ConnectTimeoutError(self, "Connection timed out") from e
        except OSError as e:
            raise NewConnectionError(self, "Failed to establish a new connection") from e
        sockets = getattr(_local, "sockets", None)
        if sockets is not None:
            # Un DOUBLE du descripteur, pas l'objet : en HTTPS, l'enveloppe TLS détache
            # l'objet rendu ici (fileno -1) et en crée un autre. Couper (shutdown) agit sur la
            # connexion elle-même, donc couper le double coupe aussi la connexion TLS.
            try:
                sockets.append(socket.socket(fileno=os.dup(sock.fileno())))
            except OSError as e:
                sock.close()
                raise NewConnectionError(self, "Failed to establish a new connection") from e
        return sock


class _GuardedHTTPConnection(_GuardedMixin, HTTPConnection):
    pass


class _GuardedHTTPSConnection(_GuardedMixin, HTTPSConnection):
    pass


class _GuardedHTTPPool(HTTPConnectionPool):
    ConnectionCls = _GuardedHTTPConnection


class _GuardedHTTPSPool(HTTPSConnectionPool):
    ConnectionCls = _GuardedHTTPSConnection


class _GuardedAdapter(HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        super().init_poolmanager(*args, **kwargs)
        self.poolmanager.pool_classes_by_scheme = {"http": _GuardedHTTPPool, "https": _GuardedHTTPSPool}


def _session():
    session = requests.Session()
    # Pas de mandataire tiré de l'environnement : on joindrait le mandataire, pas l'hôte vérifié.
    session.trust_env = False
    adapter = _GuardedAdapter(max_retries=0)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


def _port(url):
    return url.port or {"http": 80, "https": 443}.get(url.scheme)


Origin = collections.namedtuple("Origin", "scheme hostname port")


def origin(url):
    """Schéma, hôte et port que la connexion joindra : lus par urllib3, comme requests.

    Pas par urlparse : les deux ne lisent pas pareil « http://a.test\\@b.test/ » (urlparse y
    voit b.test, requests se connecte à a.test). Juger l'origine avec urlparse laisserait un
    secret partir vers a.test en le croyant chez b.test.
    """
    try:
        parsed = parse_url(url)
    except LocationParseError:
        raise BlockedAddress("") from None
    return Origin((parsed.scheme or "").lower(), (parsed.host or "").lower().strip("[]") or None, parsed.port)


def _same_origin_or_upgrade(before, after):
    """Même schéma, même hôte, même port ; ou montée de http (80) à https (443) sur le même hôte."""
    if after.hostname != before.hostname:
        return False
    if (after.scheme, _port(after)) == (before.scheme, _port(before)):
        return True
    return (before.scheme, _port(before), after.scheme, _port(after)) == ("http", 80, "https", 443)


STRIP = "strip"


def http_get(url, params=None, headers=None, secret_bound=False, max_bytes=MAX_BYTES):
    """GET gardé. Rend (statut, corps en octets, type de contenu).

    Les redirections sont suivies à la main (MAX_REDIRECTS au plus), chacune repassant la
    garde. `secret_bound` : la requête porte un secret (en-tête ou paramètre) ; une
    redirection vers une autre origine est alors refusée, sinon le secret partirait avec.
    `secret_bound=STRIP` : elle est suivie, SANS les en-têtes ni les paramètres (Microsoft
    Graph renvoie le contenu d'un fichier vers une adresse de téléchargement déjà signée).
    Lève BlockedAddress, TooLarge, ou requests.RequestException (Timeout à l'échéance).
    """
    return _request("GET", url, params=params, headers=headers, secret_bound=secret_bound, max_bytes=max_bytes)


def http_post(url, data, headers=None, max_bytes=MAX_BYTES):
    """POST gardé d'un formulaire (jetons d'accès). Aucune redirection suivie : le secret
    du formulaire n'a qu'une destination. Rend (statut, corps en octets, type de contenu)."""
    return _request("POST", url, data=data, headers=headers, secret_bound=True, max_bytes=max_bytes)


def _request(method, url, params=None, headers=None, data=None, secret_bound=False, max_bytes=MAX_BYTES):
    session = _session()
    deadline = time.monotonic() + TOTAL_TIMEOUT
    sockets = []
    _local.sockets = sockets
    timer = cut_after(TOTAL_TIMEOUT, sockets)
    try:
        current = url
        for _hop in range(MAX_REDIRECTS + 1):
            if time.monotonic() > deadline:
                raise requests.Timeout("total timeout")
            response = session.request(method, current, params=params, headers=headers, data=data,
                                       timeout=TIMEOUT, allow_redirects=False, stream=True)
            try:
                if response.is_redirect and method != "GET":
                    raise requests.TooManyRedirects("redirect on %s" % method)
                if response.is_redirect:
                    target = requests.compat.urljoin(current, response.headers.get("location", ""))
                    before, after = origin(current), origin(target)
                    if after.scheme not in ("http", "https"):
                        raise requests.TooManyRedirects("bad scheme")
                    if secret_bound and not _same_origin_or_upgrade(before, after):
                        if secret_bound != STRIP:
                            raise BlockedAddress(after.hostname or "")
                        # Le secret reste ici : la suite part sans en-têtes ni paramètres.
                        headers, secret_bound = None, False
                    current, params = target, None  # les paramètres sont déjà dans l'adresse redirigée
                    continue
                declared = response.headers.get("content-length")
                if declared and declared.isdigit() and int(declared) > max_bytes:
                    raise TooLarge()
                body = bytearray()
                for chunk in response.iter_content(65536):
                    body.extend(chunk)
                    if len(body) > max_bytes:
                        raise TooLarge()
                    if time.monotonic() > deadline:
                        raise requests.Timeout("total timeout")
                if time.monotonic() >= deadline:
                    # Coupé par le minuteur, un corps sans longueur déclarée finit comme s'il
                    # était complet : tronqué, il ne doit pas passer pour la réponse.
                    raise requests.Timeout("total timeout")
                return response.status_code, bytes(body), response.headers.get("content-type", "")
            finally:
                response.close()
        raise requests.TooManyRedirects("too many redirects")
    except (requests.RequestException, OSError):
        # Un socket coupé par le minuteur se lit comme une connexion rompue : c'est l'échéance.
        if time.monotonic() >= deadline:
            raise requests.Timeout("total timeout") from None
        raise
    finally:
        timer.cancel()
        _local.sockets = None
        for double in sockets:
            double.close()
        session.close()
