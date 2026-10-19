# -*- coding: utf-8 -*-
"""Les icônes des sites (favicons), récupérées par le serveur et rafraîchies chaque semaine.

🔴 Le serveur va chercher des adresses fournies par les usagers : c'est une porte
SSRF classique (un signet vers une adresse privée ou `http://localhost/` ferait
interroger un service interne par le serveur). La garde reprend celle de
`bf_sms_archive` : résolution UNE fois, TOUTES les
adresses publiques, connexion ÉPINGLÉE sur l'IP vérifiée (le nom reste présenté pour
le SNI et le certificat), redirections suivies à la main et revérifiées à chaque
saut, taille plafonnée. Plus la plage CGNAT 100.64.0.0/10 (réseaux privés superposés,
VPN maillés), que `ip.is_private` laisse passer.

Le cache est PAR HÔTE et invisible des usagers (aucun droit d'accès) : sinon la liste
des hôtes dirait quels sites les collègues ont en signets perso. Le widget reçoit les
icônes des seuls signets que la personne peut lire.

Le widget ne reçoit pas l'image mais son adresse : le navigateur la garde
en cache et ne la retélécharge plus à chaque ouverture du menu. L'adresse porte un
jeton signé par la base, lié au contenu de l'image : on ne peut pas la fabriquer pour
un hôte qu'on n'a pas en signet, et elle change quand l'icône change.
"""
import base64
import io
import ipaddress
import logging
import socket
from datetime import timedelta
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from odoo import _, api, fields, models
from odoo.exceptions import AccessError
from odoo.tools.misc import hmac

_logger = logging.getLogger(__name__)

try:
    import certifi
    import urllib3
except ImportError:  # pragma: no cover - dépendances d'Odoo
    certifi = urllib3 = None
try:
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None

CGNAT = ipaddress.ip_network("100.64.0.0/10")
MAX_PAGE_BYTES = 512 * 1024
MAX_ICON_BYTES = 200 * 1024
MAX_REDIRECTS = 3
ICON_SIZE = 32
ICON_ROUTE = "/bf_bookmarks/icone"
ICON_TOKEN_SCOPE = "bf_bookmarks.icone"
ICON_TOKEN_LENGTH = 32
REFRESH_DAYS = 7
RETRY_FAILED_DAYS = 1
BATCH = 50
CONNECT_TIMEOUT = 3
READ_TIMEOUT = 5
USER_AGENT = "Mozilla/5.0 (compatible; Symbifox-Signets/1.0; +https://symbifox.com)"


class FetchRefused(Exception):
    """Adresse refusée par la garde (non publique, schéma, taille)."""


class NotCheckable(FetchRefused):
    """Adresse qu'on ne visite pas exprès (interne, port non standard) : ni vivante ni
    morte, simplement non vérifiée."""


def ip_is_public(ip):
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
                or ip.is_multicast or ip.is_unspecified
                or (ip.version == 4 and ip in CGNAT))


def resolve_pinned_ip(host, port):
    """Résout `host` une seule fois ; refuse si UNE adresse n'est pas publique."""
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError) as exc:
        raise FetchRefused("résolution impossible") from exc
    if not infos:
        raise FetchRefused("aucune adresse")
    ipv4 = ipv6 = None
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError as exc:
            raise FetchRefused("adresse invalide") from exc
        if not ip_is_public(ip):
            raise NotCheckable("adresse non publique")
        if ip.version == 4 and ipv4 is None:
            ipv4 = str(ip)
        elif ip.version == 6 and ipv6 is None:
            ipv6 = str(ip)
    return ipv4 or ipv6


def http_get(url, max_bytes, truncate=False):
    """GET sûr : épinglé, sans redirection automatique, plafonné.

    Rend (statut, en-têtes, corps, url finale). Suit au plus MAX_REDIRECTS
    redirections, chacune revérifiée comme une adresse neuve. `truncate` : rend les
    `max_bytes` premiers octets au lieu de refuser (une page d'accueil ne sert que
    par son en-tête, et beaucoup dépassent le plafond).
    """
    if urllib3 is None:
        raise FetchRefused("urllib3 absent")
    for _hop in range(MAX_REDIRECTS + 1):
        parts = urlsplit(url)
        host = parts.hostname
        if parts.scheme not in ("http", "https") or not host:
            raise FetchRefused("schéma refusé")
        port = parts.port or (443 if parts.scheme == "https" else 80)
        # 80 et 443 seulement : un autre port d'une adresse publique (celle du serveur
        # lui-même comprise) ferait de la vérification un balayeur de ports.
        if port not in (80, 443):
            raise NotCheckable("port non standard")
        pinned = resolve_pinned_ip(host, port)
        path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        timeout = urllib3.Timeout(connect=CONNECT_TIMEOUT, read=READ_TIMEOUT)
        if parts.scheme == "https":
            pool = urllib3.HTTPSConnectionPool(
                pinned, port=port, cert_reqs="CERT_REQUIRED", ca_certs=certifi.where(),
                assert_hostname=host, server_hostname=host, timeout=timeout, retries=False)
        else:
            pool = urllib3.HTTPConnectionPool(pinned, port=port, timeout=timeout, retries=False)
        try:
            resp = pool.request("GET", path, headers={"Host": host, "User-Agent": USER_AGENT},
                                redirect=False, preload_content=False)
            try:
                if resp.status in (301, 302, 303, 307, 308):
                    location = resp.headers.get("Location")
                    if not location:
                        raise FetchRefused("redirection sans destination")
                    url = urljoin(url, location)
                    continue
                declared = resp.headers.get("Content-Length")
                if (not truncate and declared and declared.isdigit()
                        and int(declared) > max_bytes):
                    raise FetchRefused("trop volumineux")
                body = bytearray()
                for chunk in resp.stream(8192):
                    body.extend(chunk)
                    if len(body) > max_bytes:
                        if truncate:
                            del body[max_bytes:]
                            break
                        raise FetchRefused("trop volumineux")
                return resp.status, dict(resp.headers), bytes(body), url
            finally:
                resp.release_conn()
        finally:
            pool.close()
    raise FetchRefused("trop de redirections")


class IconLinkParser(HTMLParser):
    """Les <link rel="icon"> d'une page, avec leur taille déclarée."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag != "link":
            return
        a = {k.lower(): (v or "") for k, v in attrs}
        rel = a.get("rel", "").lower().split()
        if "icon" in rel or "apple-touch-icon" in rel:
            size = 0
            for token in a.get("sizes", "").lower().split():
                w = token.split("x")[0]
                size = max(size, int(w)) if w.isdigit() else size
            self.links.append({"href": a.get("href", ""), "size": size,
                               "svg": a.get("type", "") == "image/svg+xml"
                               or a.get("href", "").lower().endswith(".svg")})


def icon_candidates(page_url, html):
    """Les icônes à essayer, la plus proche de 32 px d'abord, /favicon.ico en dernier."""
    parser = IconLinkParser()
    try:
        parser.feed(html)
    except Exception:  # une page mal formée ne doit pas tout arrêter
        pass
    links = [l for l in parser.links if l["href"] and not l["svg"]]
    # Pillow ne lit pas le SVG. Une taille inconnue passe après les tailles ≥ 32.
    links.sort(key=lambda l: (l["size"] == 0, abs((l["size"] or 64) - ICON_SIZE)))
    urls = [urljoin(page_url, l["href"]) for l in links]
    urls.append(urljoin(page_url, "/favicon.ico"))
    seen, out = set(), []
    for u in urls:
        if u not in seen and urlsplit(u).scheme in ("http", "https"):
            seen.add(u)
            out.append(u)
    return out[:5]


def to_png(data):
    """Une icône quelconque (ICO, PNG, GIF, JPEG, WebP) en PNG carré de 32 px."""
    if Image is None:
        raise FetchRefused("Pillow absent")
    try:
        img = Image.open(io.BytesIO(data))
        if getattr(img, "format", "") == "ICO" and hasattr(img, "ico"):
            sizes = sorted(img.ico.sizes(), key=lambda s: abs(s[0] - ICON_SIZE))
            if sizes:
                img.size = sizes[0]
        img.load()
    except Exception as exc:
        raise FetchRefused("image illisible") from exc
    img = img.convert("RGBA")
    img.thumbnail((ICON_SIZE, ICON_SIZE))
    canvas = Image.new("RGBA", (ICON_SIZE, ICON_SIZE), (0, 0, 0, 0))
    canvas.paste(img, ((ICON_SIZE - img.width) // 2, (ICON_SIZE - img.height) // 2), img)
    out = io.BytesIO()
    canvas.save(out, format="PNG", optimize=True)
    return out.getvalue()


class BookmarkFavicon(models.Model):
    _name = "bf.bookmark.favicon"
    _description = "Icône d'un site (cache par hôte)"
    _rec_name = "host"

    host = fields.Char(required=True, index=True)
    scheme = fields.Char(default="https")
    image = fields.Binary(attachment=False)
    state = fields.Selection([("todo", "À chercher"), ("ok", "Trouvée"), ("none", "Aucune")],
                             default="todo", required=True, index=True)
    fetched_at = fields.Datetime(index=True)
    note = fields.Char()

    _sql_constraints = [("host_unique", "unique(host)", "Une icône par hôte.")]

    def _token(self):
        """Le jeton de l'adresse de l'icône : signé par la base, lié à l'image."""
        self.ensure_one()
        image = self.image.decode() if isinstance(self.image, bytes) else (self.image or "")
        return hmac(self.env(su=True), ICON_TOKEN_SCOPE,
                    f"{self.id}:{image}")[:ICON_TOKEN_LENGTH]

    def _url(self):
        self.ensure_one()
        return f"{ICON_ROUTE}/{self.id}/{self._token()}.png"

    @api.model
    def _http_get(self, url, max_bytes, truncate=False):
        """Point d'entrée réseau, isolé pour que les essais le remplacent."""
        return http_get(url, max_bytes, truncate=truncate)

    def _fetch_one(self):
        self.ensure_one()
        page = f"{self.scheme or 'https'}://{self.host}/"
        candidates = []
        try:
            status, headers, body, final = self._http_get(page, MAX_PAGE_BYTES, truncate=True)
            ctype = headers.get("Content-Type", headers.get("content-type", ""))
            if status == 200 and "html" in ctype.lower():
                candidates = icon_candidates(final, body.decode("utf-8", "replace"))
        except Exception as exc:
            # Page refusée ou illisible : on essaie quand même /favicon.ico. Un hôte
            # non public sera refusé là aussi, par la même garde.
            _logger.info("favicon %s : page illisible (%s)", self.host, exc)
        if not candidates:
            candidates = [urljoin(page, "/favicon.ico")]
        raison = "aucune icône lisible"
        for url in candidates:
            try:
                status, _h, body, _f = self._http_get(url, MAX_ICON_BYTES)
                if status != 200 or not body:
                    raison = f"statut {status}"
                    continue
                png = to_png(body)
            except FetchRefused as exc:
                raison = f"refusé : {exc}"
                continue
            except Exception as exc:
                raison = f"injoignable : {type(exc).__name__}"
                continue
            self.write({"image": base64.b64encode(png), "state": "ok",
                        "fetched_at": fields.Datetime.now(), "note": False})
            return True
        self.write({"state": "none", "fetched_at": fields.Datetime.now(), "note": raison[:200]})
        return False

    @api.model
    def _ensure_hosts(self, hosts):
        """Crée les hôtes inconnus (sans rien chercher : c'est la tâche planifiée)."""
        hosts = {h for h in hosts if h}
        known = set(self.sudo().search([("host", "in", list(hosts))]).mapped("host"))
        new = hosts - known
        if new:
            self.sudo().create([{"host": h} for h in new])
            cron = self.env.ref("bf_bookmarks.cron_refresh_favicons", raise_if_not_found=False)
            if cron:
                cron.sudo()._trigger()
        return new

    @api.model
    def _cron_refresh(self):
        """Nouveaux hôtes, icônes de plus de 7 jours, échecs de plus d'un jour."""
        Bookmark = self.env["bf.bookmark"].sudo()
        hosts = set(Bookmark.search([]).mapped("host"))
        self._ensure_hosts(hosts)
        now = fields.Datetime.now()
        due = self.sudo().search([
            ("host", "in", list(hosts)),
            "|", "|", ("state", "=", "todo"),
            "&", ("state", "=", "ok"), ("fetched_at", "<", now - timedelta(days=REFRESH_DAYS)),
            "&", ("state", "=", "none"), ("fetched_at", "<", now - timedelta(days=RETRY_FAILED_DAYS)),
        ], limit=BATCH, order="fetched_at asc nulls first")
        for favicon in due:
            favicon._fetch_one()
            if not self.env.context.get("bf_bookmarks_no_commit"):
                self.env.cr.commit()
        if len(due) == BATCH:
            self.env.ref("bf_bookmarks.cron_refresh_favicons")._trigger()
        return len(due)


class Bookmark(models.Model):
    _inherit = "bf.bookmark"

    host = fields.Char(compute="_compute_host", store=True, index=True)

    @api.depends("url")
    def _compute_host(self):
        for bookmark in self:
            bookmark.host = (urlsplit(bookmark.url or "").hostname or "").lower() or False

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        self.env["bf.bookmark.favicon"]._ensure_hosts(set(records.mapped("host")))
        return records

    def write(self, vals):
        result = super().write(vals)
        if "url" in vals:
            self.env["bf.bookmark.favicon"]._ensure_hosts(set(self.mapped("host")))
        return result

    def _check_derived_fields(self):
        # L'hôte suit l'adresse : écrit directement, il servait l'icône d'un AUTRE hôte
        # (et sondait le cache des icônes sans créer de signet vers lui).
        super()._check_derived_fields()
        for mark in self.sudo().with_context(active_test=False):
            expected = (urlsplit(mark.url or "").hostname or "").lower() or False
            if mark.host != expected:
                raise AccessError(_("L'hôte d'un signet suit son adresse."))

    def _icons(self):
        """{id: adresse de l'icône} pour ces signets (que l'appelant a déjà lus avec
        ses droits). Une adresse par hôte : le navigateur la garde en cache."""
        hosts = set(self.mapped("host"))
        icons = self.env["bf.bookmark.favicon"].sudo().search(
            [("host", "in", list(hosts)), ("state", "=", "ok")])
        by_host = {i.host: i._url() for i in icons if i.image}
        return {b.id: by_host[b.host] for b in self if b.host in by_host}
