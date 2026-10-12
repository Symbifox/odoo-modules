"""Names of new client folders (18.0.4.3.0).

A client folder is reached from Windows, macOS and Linux sync clients, and
from SharePoint-like tools: its name keeps to what all of them accept.
Numbering follows what is already on disk, never a counter kept in Odoo: the
folders are created by hand too (in a file manager, by an onboarding script).
"""

import re
import unicodedata

# Refused by Windows (and so by the Nextcloud desktop client there). A percent
# sign goes too: the path helpers refuse a name that would url-decode.
FORBIDDEN = re.compile(r'[\\/:*?"<>|%\x00-\x1f]')
TOKEN = re.compile(r"\{(N+)\}")
# Device names Windows reserves, with or without an extension.
RESERVED = re.compile(r"^(CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9])(\..*)?$", re.IGNORECASE)
MAX_NAME = 120


def clean_name(name, max_len=MAX_NAME):
    """A name Nextcloud and every sync client accept: forbidden characters
    become spaces, runs of spaces collapse, no leading dot or space, no
    trailing dot or space, no reserved device name, at most `max_len`
    characters (the extension of a file name is kept)."""
    name = unicodedata.normalize("NFC", name or "")
    name = FORBIDDEN.sub(" ", name)
    name = re.sub(r"\s+", " ", name).lstrip(". ").rstrip(". ")
    if len(name) > max_len:
        stem, dot, ext = name.rpartition(".")
        if dot and stem and len(ext) <= 10:
            name = stem[: max_len - len(ext) - 1].rstrip(". ") + "." + ext
        else:
            name = name[:max_len].rstrip(". ")
    if RESERVED.match(name):
        name = "_" + name
    return name


def numbered(pattern):
    """A regex reading the number out of folder names made with `pattern`, or
    None when the pattern carries no number. « 2026-01 Archives » is not
    the folder number 2026 of « {NNN} - {client} »."""
    pattern = pattern or "{NNN} - {client}"
    if not TOKEN.search(pattern):
        return None
    out, pos, seen = "^", 0, set()
    for m in re.finditer(r"\{(N+)\}|\{client\}", pattern):
        out += re.escape(pattern[pos:m.start()])
        kind = "num" if m.group(1) else "client"
        out += ("(?P<%s>%s)" % (kind, r"\d+" if kind == "num" else ".+")) if kind not in seen else "(?:.+)"
        seen.add(kind)
        pos = m.end()
    return re.compile(out + re.escape(pattern[pos:]) + "$")


def readable(pattern):
    """Whether folders made with `pattern` can be read back (number and
    client): a literal that clean_name would alter (« {NNN} : {client} »)
    would make every folder unreadable, and the numbering restart at 1."""
    rx = numbered(pattern)
    if rx is None:
        return True
    m = rx.match(build_name(pattern, 7, 0, "Client essai"))
    return bool(m) and m.group("num").lstrip("0") == "7"


def next_number(names, pattern=None):
    """(next number, digits used so far) from the folders made with `pattern`."""
    rx = numbered(pattern)
    if rx is None:
        return 1, 0
    numbers = [m.group("num") for m in (rx.match(n or "") for n in names) if m]
    if not numbers:
        return 1, 0
    return max(int(n) for n in numbers) + 1, max(len(n) for n in numbers)


def same_client(names, client, pattern=None):
    """The existing folder that already carries this client's name, numbered
    or not, or None (a retried creation must not make « 031 - X » next to
    « 030 - X »)."""
    wanted = clean_name(client).casefold()
    rx = numbered(pattern)
    for n in names:
        bare = n
        if rx is not None:
            m = rx.match(n)
            if m and "client" in m.groupdict():
                bare = m.group("client")
        if clean_name(bare).casefold() == wanted:
            return n
    return None


def build_name(pattern, number, width, client):
    """Fill `pattern`: {N…} is the number on at least that many digits (or as
    many as the existing folders use), {client} the cleaned client name."""
    pattern = pattern or "{NNN} - {client}"

    def numero(match):
        return str(number).zfill(max(len(match.group(1)), width))

    return clean_name(TOKEN.sub(numero, pattern).replace("{client}", clean_name(client)))
