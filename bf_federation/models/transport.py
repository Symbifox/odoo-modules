"""Ce qui traverse entre deux instances, et comment c'est signé.

Rien d'un locataire n'arrive balisé chez l'autre : les corps HTML sont réduits
en texte à l'export, ré-échappés à l'import. Aucun identifiant n'est réutilisé
tel quel : chaque côté ne connaît l'autre que par la référence que l'autre lui
a donnée, et les personnes voyagent en nom et courriel.
"""

import hashlib
import hmac
import html
import json
import secrets
import time
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

PROTOCOL = "symbifox-federation/1"
SIGNATURE_WINDOW = 300          # secondes d'écart toléré sur l'horodatage
PRIVATE_MARKERS = ("🔒", "[privé]", "[prive]", "[private]")
HEADER_PEER = "X-Federation-Peer"
HEADER_TIMESTAMP = "X-Federation-Timestamp"
HEADER_NONCE = "X-Federation-Nonce"
HEADER_SIGNATURE = "X-Federation-Signature"


class _ToText(HTMLParser):
    """HTML → texte : puces, liens (texte + url) et sauts de ligne conservés."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.lines = [""]
        self._href = None
        self._link_text = []

    def _newline(self):
        if self.lines[-1] != "":
            self.lines.append("")

    def handle_starttag(self, tag, attrs):
        if tag in ("p", "div", "br", "tr", "h1", "h2", "h3", "h4", "table"):
            self._newline()
        elif tag == "li":
            self._newline()
            self.lines[-1] = "• "
        elif tag == "a":
            self._href = dict(attrs).get("href")
            self._link_text = []
        elif tag == "img":
            self.lines[-1] += "[image]"

    def handle_endtag(self, tag):
        if tag == "a":
            text = "".join(self._link_text).strip()
            href = self._href or ""
            if href and href not in text and not href.startswith(("/web/", "#", "mailto:" + text)):
                self.lines[-1] += f" ({href})"
            self._href = None
        elif tag in ("p", "div", "li", "tr", "h1", "h2", "h3", "h4", "ul", "ol"):
            self._newline()

    def handle_data(self, data):
        data = data.replace("\r", "").replace("\n", " ")
        if self._href is not None:
            self._link_text.append(data)
        self.lines[-1] += data

    def text(self):
        out = []
        for line in (" ".join(l.split()) for l in self.lines):
            if line == "" and (not out or out[-1] == ""):
                continue
            out.append(line)
        return "\n".join(out).strip()


def html_to_text(fragment):
    if not fragment:
        return ""
    parser = _ToText()
    parser.feed(str(fragment))
    parser.close()
    return parser.text()


def text_to_html(text):
    if not text:
        return ""
    return "<p>" + "<br/>".join(html.escape(line) for line in text.split("\n")) + "</p>"


def is_private(body_html):
    text = html_to_text(body_html).lstrip().lower()
    return text.startswith(tuple(m.lower() for m in PRIVATE_MARKERS))


def fingerprint(*parts):
    return hashlib.sha1("\x1f".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:16]


def new_secret():
    return secrets.token_urlsafe(32)


def new_code():
    return secrets.token_urlsafe(18)


def new_nonce():
    return secrets.token_hex(16)


def canonical_body(payload):
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def sign(secret, timestamp, nonce, body):
    msg = f"{timestamp}.{nonce}.".encode("utf-8") + body
    return hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()


def signature_ok(secret, timestamp, nonce, body, given, now=None):
    try:
        ts = int(timestamp)
    except (TypeError, ValueError):
        return False
    now = now if now is not None else int(time.time())
    if abs(now - ts) > SIGNATURE_WINDOW:
        return False
    if not given or not nonce:
        return False
    return hmac.compare_digest(sign(secret, ts, nonce, body), str(given))


# --- Le jour d'échéance : la seule chose qu'une échéance transporte ------------
def safe_zone(name):
    try:
        return ZoneInfo(name or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def day_in_zone(dt, tz_name):
    """Jour civil (YYYY-MM-DD) d'un datetime naïf UTC d'Odoo dans un fuseau."""
    if not dt:
        return False
    if isinstance(dt, str):
        dt = datetime.strptime(dt, "%Y-%m-%d %H:%M:%S")
    return dt.replace(tzinfo=timezone.utc).astimezone(safe_zone(tz_name)).strftime("%Y-%m-%d")


def noon_in_zone_utc(day, tz_name):
    """Le jour donné, à midi dans le fuseau, rendu en datetime naïf UTC."""
    if not day:
        return False
    local = datetime.strptime(day, "%Y-%m-%d").replace(hour=12, tzinfo=safe_zone(tz_name))
    return local.astimezone(timezone.utc).replace(tzinfo=None)


def write_deadline_day(task, day, tz_name):
    """Écrit le jour voulu, relit, corrige d'un jour si un module a redécoré l'heure.

    Certains modules reposent l'heure d'une échéance dans le fuseau de
    l'utilisateur qui écrit ; quand ce fuseau est loin, le jour civil peut
    glisser. La relecture est la seule preuve.
    """
    task.write({"date_deadline": noon_in_zone_utc(day, tz_name)})
    if not day:
        return
    for _attempt in range(2):
        got = day_in_zone(task.date_deadline, tz_name)
        if got == day:
            return
        delta = (datetime.strptime(day, "%Y-%m-%d") - datetime.strptime(got, "%Y-%m-%d")).days
        task.write({"date_deadline": task.date_deadline + timedelta(days=delta)})
