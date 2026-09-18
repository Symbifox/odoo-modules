"""IMAP fetch and RFC 2822 parsing helpers for bf_email_management.

Used by ``bf.email._cron_sync_imap`` and the reroute / backfill wizards.
Mirrors the logic of ``scripts/import_personal_email.py`` so the IMAP
ingestion path on the Odoo side stays consistent with the CLI tool.
"""

import base64
import email
import email.policy
import html as html_mod
import imaplib
import logging
import re
import ssl
from email.utils import parseaddr, parsedate_to_datetime

_logger = logging.getLogger(__name__)


# Folders the live cron polls. Backfill wizard targets a single folder.
DEFAULT_LIVE_FOLDERS = ("INBOX", "Sent")

# Folders we never poll: trash / drafts / junk produce noise.
EXCLUDED_FOLDER_PATTERNS = re.compile(
    r"^(Trash|Junk|Drafts|Spam)(/|$)", re.IGNORECASE
)


class ImapConnectionError(Exception):
    """Raised when the IMAP connection or auth fails."""


class ImapInjectionError(ValueError):
    """Raised when an IMAP command argument carries illegal control chars.

    ``imaplib`` does not validate its arguments, so a CR/LF embedded in a
    mailbox name, UID or header value can inject a second command into the
    authenticated session. We reject such values instead of sending them.
    """


def imap_quote_mailbox(name):
    """Quote a mailbox name as an IMAP quoted-string, safely.

    IMAP quoted-strings cannot carry CR/LF (RFC 3501), so we reject them
    outright; backslash and double-quote are escaped. Use this for every
    folder name interpolated into a command (SELECT, COPY, …).
    """
    text = "" if name is None else str(name)
    if "\r" in text or "\n" in text:
        raise ImapInjectionError("CR/LF not allowed in IMAP mailbox name")
    return '"%s"' % text.replace("\\", "\\\\").replace('"', '\\"')


def imap_reject_crlf(value, label="argument"):
    """Return ``str(value)`` after rejecting embedded CR/LF (IMAP injection)."""
    text = "" if value is None else str(value)
    if "\r" in text or "\n" in text:
        raise ImapInjectionError("CR/LF not allowed in IMAP %s" % label)
    return text


def imap_uid_token(uid):
    """Validate a UID (or UID set/range) and return it as a safe IMAP token.

    Accepts only digits plus set/range punctuation (``, : *``) so the value
    cannot smuggle spaces or CR/LF into a UID command.
    """
    text = "" if uid is None else str(uid).strip()
    if not re.fullmatch(r"[0-9][0-9,:*]*", text):
        raise ImapInjectionError("Invalid IMAP UID token: %r" % (uid,))
    return text


def open_connection(host, port, user, password, timeout=30):
    """Open an authenticated IMAP4_SSL connection.

    Caller is responsible for ``logout()``. We deliberately do not return
    a context manager — Odoo cron methods commit between batches and we
    keep the connection alive across them.
    """
    try:
        ctx = ssl.create_default_context()
        conn = imaplib.IMAP4_SSL(host, int(port), ssl_context=ctx, timeout=timeout)
        conn.login(user, password)
        return conn
    except (imaplib.IMAP4.error, OSError) as exc:
        raise ImapConnectionError(f"IMAP login failed for {user}@{host}: {exc}") from exc


def select_folder(conn, folder, readonly=True):
    """SELECT (or EXAMINE) a folder. Returns True if it exists and is selectable.

    The mailbox name is quoted/validated via ``imap_quote_mailbox`` so a
    crafted folder string cannot inject IMAP commands.
    """
    try:
        status, _data = conn.select(imap_quote_mailbox(folder), readonly=readonly)
        return status == "OK"
    except (imaplib.IMAP4.error, ImapInjectionError):
        return False


def ensure_folder(conn, folder):
    """Best-effort CREATE (and SUBSCRIBE) of a destination mailbox.

    A rule names a folder in a text field; nothing guarantees the folder
    exists on the server. Without this, the COPY is refused, the message stays
    in the INBOX, and the only trace is a warning in the log.

    Deliberately returns nothing to act on: an « already exists » is reported
    as NO by some servers and as OK by others, and a CREATE that fails for a
    real reason (quota, permissions) is indistinguishable here. The caller
    retries the COPY, and *that* is the verdict — we do not guess.

    SUBSCRIBE matters more than it looks: Migadu, Dovecot and Gmail all hide
    an unsubscribed folder from most clients, so a message would land
    somewhere the owner cannot see.
    """
    try:
        quoted = imap_quote_mailbox(folder)
    except ImapInjectionError:
        return
    for verb in ("create", "subscribe"):
        try:
            getattr(conn, verb)(quoted)
        except (imaplib.IMAP4.error, OSError):
            _logger.debug(
                "bf.email IMAP: %s %r sans effet", verb, folder, exc_info=True,
            )


def search_uids_above(conn, last_uid):
    """Return list of UIDs strictly greater than ``last_uid`` (sorted asc)."""
    next_uid = (int(last_uid) if last_uid else 0) + 1
    status, data = conn.uid("SEARCH", None, f"UID {next_uid}:*")
    if status != "OK" or not data or not data[0]:
        return []
    raw = data[0]
    if isinstance(raw, bytes):
        raw = raw.decode("ascii", errors="ignore")
    uids = [int(x) for x in raw.split() if x.isdigit() and int(x) >= next_uid]
    return sorted(uids)


def search_uids_in_range(conn, date_from=None, date_to=None):
    """Return UIDs filtered by SINCE/BEFORE (used by backfill wizard).

    Dates must be ``datetime.date`` instances. Both bounds are optional.
    """
    parts = []
    if date_from:
        parts.extend(["SINCE", date_from.strftime("%d-%b-%Y")])
    if date_to:
        parts.extend(["BEFORE", date_to.strftime("%d-%b-%Y")])
    if not parts:
        parts = ["ALL"]
    status, data = conn.uid("SEARCH", None, *parts)
    if status != "OK" or not data or not data[0]:
        return []
    raw = data[0]
    if isinstance(raw, bytes):
        raw = raw.decode("ascii", errors="ignore")
    return sorted(int(x) for x in raw.split() if x.isdigit())


def fetch_rfc822(conn, uid):
    """Fetch a single UID. Returns raw bytes or None on failure."""
    status, data = conn.uid("FETCH", str(uid), "(RFC822)")
    if status != "OK" or not data or not data[0]:
        return None
    payload = data[0]
    if isinstance(payload, tuple):
        return payload[1]
    return None


def fetch_headers_bulk(conn, uids):
    """Fetch DATE/FROM/SUBJECT/MESSAGE-ID + FLAGS for many UIDs in one round-trip.

    Used by the IMAP browser to populate the line tree without pulling
    full RFC 2822 bodies (which can include MB-scale attachments). Returns
    ``{uid: (parsed_email_message, seen_bool)}`` per UID — ``seen_bool``
    is True when the ``\\Seen`` flag is set.
    """
    if not uids:
        return {}
    uid_set = ",".join(str(u) for u in uids)
    status, data = conn.uid(
        "FETCH",
        uid_set,
        "(FLAGS BODY.PEEK[HEADER.FIELDS (DATE FROM SUBJECT MESSAGE-ID)])",
    )
    if status != "OK" or not data:
        return {}
    result = {}
    # IMAP returns alternating tuples and ")" close-parens; the tuple shape
    # is (b"<uid> (UID 123 FLAGS (\\Seen) ... {<size>}", header_bytes).
    for item in data:
        if not isinstance(item, tuple) or len(item) < 2:
            continue
        prefix, header_bytes = item[0], item[1]
        if isinstance(prefix, bytes):
            prefix = prefix.decode("ascii", errors="ignore")
        m = re.search(r"UID (\d+)", prefix or "")
        if not m:
            continue
        uid = int(m.group(1))
        # FLAGS extraction: ``FLAGS (\Seen \Answered)`` → look for \Seen token.
        flags_match = re.search(r"FLAGS \(([^)]*)\)", prefix or "")
        seen = False
        if flags_match:
            flags_blob = flags_match.group(1)
            seen = "\\Seen" in flags_blob
        if not header_bytes:
            continue
        try:
            result[uid] = (parse_rfc822(header_bytes), seen)
        except Exception:
            _logger.debug("fetch_headers_bulk: parse failed for UID %s", uid, exc_info=True)
    return result


def parse_rfc822(raw_bytes):
    """Parse raw RFC 2822 bytes into a ``email.message.EmailMessage``."""
    return email.message_from_bytes(raw_bytes, policy=email.policy.default)


def extract_body(msg):
    """Return ``(body_html, body_plain)`` from a parsed message."""
    body_html = ""
    body_plain = ""
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            disp = str(part.get("Content-Disposition", ""))
            if "attachment" in disp:
                continue
            payload = part.get_payload(decode=True)
            if payload is None:
                continue
            charset = part.get_content_charset() or "utf-8"
            try:
                text = payload.decode(charset, errors="replace")
            except (LookupError, UnicodeDecodeError):
                text = payload.decode("utf-8", errors="replace")
            if ct == "text/html" and not body_html:
                body_html = text
            elif ct == "text/plain" and not body_plain:
                body_plain = text
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            try:
                text = payload.decode(charset, errors="replace")
            except (LookupError, UnicodeDecodeError):
                text = payload.decode("utf-8", errors="replace")
            if msg.get_content_type() == "text/html":
                body_html = text
            else:
                body_plain = text

    if body_html:
        m = re.search(r"<body[^>]*>(.*)</body>", body_html, re.DOTALL | re.IGNORECASE)
        if m:
            body_html = m.group(1).strip()
    return body_html, body_plain


def extract_attachments(msg):
    """Return a list of ``(filename, content_bytes)`` from message parts."""
    out = []
    if not msg.is_multipart():
        return out
    for part in msg.walk():
        disp = str(part.get("Content-Disposition", ""))
        if "attachment" not in disp:
            continue
        filename = part.get_filename()
        if not filename:
            continue
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        out.append((str(filename), payload))
    return out


def parse_date(date_header):
    """Parse RFC 2822 Date header into Odoo string format. Returns None on failure."""
    if not date_header:
        return None
    try:
        dt = parsedate_to_datetime(str(date_header))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is not None:
        # Convert to UTC naive (Odoo stores datetimes as UTC naive)
        from datetime import timezone
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def parse_thread_headers(msg):
    """Return ``(in_reply_to, thread_root_id)``.

    ``thread_root_id`` is the first Message-ID in the References header,
    falling back to In-Reply-To, then to the message's own Message-ID.
    """
    in_reply_to = str(msg.get("In-Reply-To", "")).strip() or False
    references = str(msg.get("References", "")).strip()
    msg_id = str(msg.get("Message-ID", "")).strip() or False

    thread_root = False
    if references:
        ids = re.findall(r"<[^>]+>", references)
        if ids:
            thread_root = ids[0]
    if not thread_root:
        thread_root = in_reply_to or msg_id
    return in_reply_to, thread_root


def is_outbound_address(email_addr, configured_user):
    """True if ``email_addr`` matches the IMAP user (we sent it)."""
    if not email_addr or not configured_user:
        return False
    _name, bare = parseaddr(email_addr)
    return bare.strip().lower() == configured_user.strip().lower()


def attachment_to_b64(content_bytes):
    """Encode bytes to ASCII base64 (for ir.attachment.datas)."""
    return base64.b64encode(content_bytes).decode("ascii")


def unwrap_double_encoded_html(stored_body):
    """Reverse double-HTML-escape if the chatter stored ``&lt;p&gt;…``.

    Mirrors the post-fix in scripts/import_personal_email.py:264.
    """
    if not stored_body:
        return stored_body
    if "&lt;div" in stored_body or "&lt;table" in stored_body or "&lt;p " in stored_body:
        fixed = html_mod.unescape(stored_body)
        if fixed.startswith("<p>") and fixed.endswith("</p>"):
            fixed = fixed[3:-4]
        return fixed
    return stored_body


# ----------------------------------------------------------------------
# Vérification d'UID et découverte des dossiers
# ----------------------------------------------------------------------

def normalize_message_id(value):
    """Message-ID comparable : espaces retirés, chevrons retirés.

    Les serveurs restituent l'en-tête tantôt avec ses chevrons, tantôt sans,
    et parfois replié sur deux lignes. Comparer les chaînes brutes fait donc
    échouer des correspondances pourtant exactes.
    """
    text = "" if value is None else str(value)
    text = " ".join(text.split())
    return text.strip().lstrip("<").rstrip(">")


def uid_carries_message_id(conn, uid, message_id):
    """Ce ``uid`` porte-t-il bien ce Message-ID dans la boîte SELECTionnée ?

    Retourne ``True``, ``False``, ou ``None`` quand la question n'a pas pu
    être posée (erreur réseau, réponse illisible) — l'appelant décide alors
    s'il se rabat sur une recherche par en-tête.

    ⚠️ Un UID n'a de sens que dans SA boîte. Un ``UID COPY`` visant un UID
    absent reçoit ``OK`` sans rien copier (RFC 3501 : les commandes UID
    ignorent silencieusement les UID inconnus), donc aucune garde placée sur
    le statut de la réponse ne peut rattraper un UID périmé. Il faut le
    vérifier avant de s'en servir.
    """
    if not uid or not message_id:
        return None
    try:
        status, data = conn.uid(
            "FETCH", imap_uid_token(uid),
            "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)])",
        )
    except (imaplib.IMAP4.error, ImapInjectionError, OSError):
        _logger.debug("uid_carries_message_id: FETCH %s échoué", uid, exc_info=True)
        return None
    if status != "OK":
        return None
    if not data or all(not isinstance(x, tuple) for x in data):
        # Réponse vide = l'UID n'existe pas dans cette boîte. C'est une
        # réponse, pas une panne : le rappeler comme un franc « non ».
        return False
    for item in data:
        if not isinstance(item, tuple) or len(item) < 2 or not item[1]:
            continue
        try:
            msg = parse_rfc822(item[1])
        except Exception:
            continue
        found = normalize_message_id(msg.get("Message-ID", ""))
        if found and found == normalize_message_id(message_id):
            return True
    return False


# `(\HasNoChildren) "/" "INBOX"` — drapeaux, délimiteur, nom. Le nom peut
# être cité ou nu ; `rsplit` sur l'espace le coupait dès qu'il en contenait un.
_LIST_LINE_RE = re.compile(
    r'^\((?P<flags>[^)]*)\)\s+(?P<delim>"(?:[^"\\]|\\.)*"|NIL)\s+(?P<name>.+)$'
)


def _unquote_mailbox(raw):
    text = (raw or "").strip()
    if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        text = text[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return text


def list_folders(conn):
    """``LIST`` décodé : ``[{name, delimiter, has_children, noselect}]``.

    Sans ``STATUS`` — compter les messages du serveur coûte un aller-retour
    par dossier, et l'arbre de la boîte de réception compte les lignes
    ``bf.email``, pas les messages du serveur.
    """
    try:
        status, raw = conn.list()
    except (imaplib.IMAP4.error, OSError):
        _logger.debug("list_folders: LIST échoué", exc_info=True)
        return []
    if status != "OK" or not raw:
        return []
    out = []
    for line in raw:
        if not line:
            continue
        # Un nom de boîte non ASCII revient en littéral : (flags, nom_bytes).
        if isinstance(line, tuple):
            head = line[0].decode("utf-8", errors="replace") if isinstance(line[0], bytes) else str(line[0])
            tail = line[1].decode("utf-8", errors="replace") if isinstance(line[1], bytes) else str(line[1])
            decoded = "%s%s" % (head.split("{", 1)[0], tail)
        elif isinstance(line, bytes):
            decoded = line.decode("utf-8", errors="replace")
        else:
            decoded = str(line)
        match = _LIST_LINE_RE.match(decoded.strip())
        if not match:
            continue
        name = _unquote_mailbox(match.group("name"))
        if not name:
            continue
        delim = match.group("delim")
        delimiter = "" if delim == "NIL" else _unquote_mailbox(delim)
        flags = match.group("flags") or ""
        out.append({
            "name": name,
            "delimiter": delimiter,
            "has_children": "\\HasChildren" in flags,
            "noselect": "\\Noselect" in flags or "\\NonExistent" in flags,
        })
    return out
