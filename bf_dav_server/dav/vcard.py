# Part of bf_dav_server. See LICENSE file for full copyright and licensing details.
"""vCard 3.0/4.0 reading and vCard 3.0 writing, independent of the ORM.

Odoo holds one email, one phone, one mobile and one address per contact.
Everything else a client writes on a card (second email, other numbers,
birthday, notes, social profiles, X-* properties) is kept as raw lines and
handed back unchanged, so a phone that syncs a contact never strips it.
"""

import base64
import re
from dataclasses import dataclass, field

from .ical import CRLF, fold, unfold

MAX_VCF_BYTES = 2 * 1024 * 1024

# Properties rebuilt from Odoo fields.
MAPPED = {"BEGIN", "END", "VERSION", "UID", "FN", "N", "ORG", "TITLE", "EMAIL", "TEL", "ADR",
          "URL", "CATEGORIES", "PHOTO", "REV", "PRODID", "X-ABSHOWAS", "KIND"}


class VcardError(ValueError):
    def __init__(self, message, condition="valid-address-data"):
        super().__init__(message)
        self.condition = condition


def esc(value):
    value = (value or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
    return value.replace("\r\n", "\\n").replace("\n", "\\n")


def unesc(value):
    out, i = [], 0
    while i < len(value):
        ch = value[i]
        if ch == "\\" and i + 1 < len(value):
            nxt = value[i + 1]
            out.append("\n" if nxt in "nN" else nxt)
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def split_unescaped(value, sep):
    parts, cur, i = [], [], 0
    while i < len(value):
        ch = value[i]
        if ch == "\\" and i + 1 < len(value):
            cur.append(value[i:i + 2])
            i += 2
            continue
        if ch == sep:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
        i += 1
    parts.append("".join(cur))
    return parts


@dataclass
class Card:
    uid: str = ""
    fn: str = ""
    n_family: str = ""
    n_given: str = ""
    org: str = None
    title: str = None
    emails: list = field(default_factory=list)      # (value, types)
    tels: list = field(default_factory=list)        # (value, types)
    adr: dict = None                                 # first address
    url: str = None
    categories: list = None
    photo: bytes = None
    photo_present: bool = False
    is_company: bool = False
    extra: list = field(default_factory=list)       # raw lines, unfolded
    version: str = "3.0"


def _split_line(line):
    in_quote = False
    for i, ch in enumerate(line):
        if ch == '"':
            in_quote = not in_quote
        elif ch == ":" and not in_quote:
            return line[:i], line[i + 1:]
    return line, ""


def _params(head):
    parts = head.split(";")
    name = parts[0]
    if "." in name:  # item1.EMAIL grouping
        name = name.split(".", 1)[1]
    params = {}
    for part in parts[1:]:
        if "=" in part:
            k, v = part.split("=", 1)
            params.setdefault(k.strip().upper(), []).extend(
                x.strip().strip('"').upper() for x in v.split(","))
        else:  # vCard 2.1 bare type
            params.setdefault("TYPE", []).append(part.strip().upper())
    return name.upper(), params


def parse_card(text):
    if isinstance(text, bytes):
        if len(text) > MAX_VCF_BYTES:
            raise VcardError("card too large", "max-resource-size")
        try:
            text = text.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise VcardError("card is not UTF-8") from exc
    lines = [ln for ln in unfold(text).split("\n") if ln.strip()]
    if not lines or lines[0].strip().upper() != "BEGIN:VCARD" or lines[-1].strip().upper() != "END:VCARD":
        raise VcardError("not a single vCard")
    if sum(1 for ln in lines if ln.strip().upper() == "BEGIN:VCARD") != 1:
        raise VcardError("several vCards in one resource")
    card = Card()
    for line in lines[1:-1]:
        head, value = _split_line(line)
        name, params = _params(head)
        types = set(params.get("TYPE", []))
        if name == "VERSION":
            card.version = value.strip()
        elif name == "UID":
            card.uid = value.strip()
        elif name == "FN":
            card.fn = unesc(value).strip()
        elif name == "N":
            parts = [unesc(p) for p in split_unescaped(value, ";")] + ["", ""]
            card.n_family, card.n_given = parts[0].strip(), parts[1].strip()
        elif name == "ORG":
            card.org = unesc(split_unescaped(value, ";")[0]).strip()
        elif name == "TITLE":
            card.title = unesc(value).strip()
        elif name == "EMAIL":
            card.emails.append((value.strip().removeprefix("mailto:"), types, line))
        elif name == "TEL":
            card.tels.append((value.strip().removeprefix("tel:"), types, line))
        elif name == "ADR":
            if card.adr is None:
                parts = [unesc(p) for p in split_unescaped(value, ";")] + [""] * 7
                card.adr = {"street2": parts[1].strip(), "street": parts[2].strip(),
                            "city": parts[3].strip(), "state": parts[4].strip(),
                            "zip": parts[5].strip(), "country": parts[6].strip()}
            else:
                card.extra.append(line)
        elif name == "URL":
            if card.url is None:
                card.url = unesc(value).strip()
            else:
                card.extra.append(line)
        elif name == "CATEGORIES":
            card.categories = (card.categories or []) + [
                unesc(c).strip() for c in split_unescaped(value, ",") if unesc(c).strip()]
        elif name == "PHOTO":
            card.photo_present = True
            card.photo = _photo_bytes(value, params)
        elif name == "X-ABSHOWAS":
            card.is_company = value.strip().upper() == "COMPANY"
        elif name == "KIND":
            card.is_company = value.strip().lower() == "org"
        elif name in ("REV", "PRODID", "BEGIN", "END"):
            continue
        else:
            card.extra.append(line)
    if not card.uid:
        raise VcardError("card without UID")
    if not (card.fn or card.n_family or card.n_given or card.org):
        raise VcardError("card without a name")
    return card


def _photo_bytes(value, params):
    value = value.strip()
    if value.startswith("data:"):
        m = re.match(r"^data:[^;,]*(;base64)?,(.*)$", value, re.S)
        if not m or not m.group(1):
            return None
        value = m.group(2)
    elif "B" not in params.get("ENCODING", []) and "BASE64" not in params.get("ENCODING", []):
        return None  # a URL: not fetched
    try:
        return base64.b64decode(value, validate=False)
    except (ValueError, TypeError):
        return None


def display_name(card):
    if card.fn:
        return card.fn
    name = " ".join(p for p in (card.n_given, card.n_family) if p)
    return name or card.org or ""


def build_card(data, extra=()):
    """``data``: dict with uid, name, is_company, org, title, email, phone,
    mobile, adr (dict), url, categories, photo (base64 str), rev (datetime)."""
    lines = ["BEGIN:VCARD", "VERSION:3.0", "PRODID:-//Symbifox//bf_dav_server//EN",
             "UID:%s" % data["uid"]]
    name = data.get("name") or ""
    lines.append("FN:%s" % esc(name))
    if data.get("is_company"):
        lines.append("N:;;;;")
        lines.append("ORG:%s" % esc(name))
        lines.append("X-ABShowAs:COMPANY")
    else:
        given, _sep, family = name.rpartition(" ")
        if not given:
            given, family = family, ""
        lines.append("N:%s;%s;;;" % (esc(family), esc(given)))
        if data.get("org"):
            lines.append("ORG:%s" % esc(data["org"]))
    if data.get("title"):
        lines.append("TITLE:%s" % esc(data["title"]))
    if data.get("email"):
        lines.append("EMAIL;TYPE=INTERNET,WORK:%s" % data["email"])
    if data.get("phone"):
        lines.append("TEL;TYPE=WORK,VOICE:%s" % data["phone"])
    if data.get("mobile"):
        lines.append("TEL;TYPE=CELL:%s" % data["mobile"])
    adr = data.get("adr") or {}
    if any(adr.values()):
        lines.append("ADR;TYPE=WORK:;%s;%s;%s;%s;%s;%s" % tuple(
            esc(adr.get(k) or "") for k in ("street2", "street", "city", "state", "zip", "country")))
    if data.get("url"):
        lines.append("URL:%s" % data["url"])
    if data.get("categories"):
        lines.append("CATEGORIES:%s" % ",".join(esc(c) for c in data["categories"]))
    if data.get("photo"):
        kind = "PNG" if data["photo"].startswith("iVBOR") else "JPEG"
        lines.append("PHOTO;ENCODING=b;TYPE=%s:%s" % (kind, data["photo"]))
    if data.get("rev"):
        lines.append("REV:%s" % data["rev"].strftime("%Y%m%dT%H%M%SZ"))
    lines += list(extra)
    lines.append("END:VCARD")
    return CRLF.join(fold(ln) for ln in lines) + CRLF
