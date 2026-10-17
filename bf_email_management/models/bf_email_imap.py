"""IMAP fetch and RFC 2822 parsing helpers for bf_email_management.

Used by ``bf.email._cron_sync_imap`` and the reroute / backfill wizards.
Mirrors the logic of ``scripts/import_personal_email.py`` so the IMAP
ingestion path on the Odoo side stays consistent with the CLI tool.
"""

import base64
import codecs
import email
import email.header
import email.parser
import email.quoprimime
import email.policy
import fnmatch
import html as html_mod
import imaplib
import logging
import re
import socket
import ssl
from email.utils import parseaddr, parsedate_to_datetime

_logger = logging.getLogger(__name__)


# Le dossier de nos envois. Comparé sans la casse : l'ingestion y lit la
# direction « out », la boîte de réception y prend nos copies d'envoi et le
# miroir y surveille leur archivage.
SENT_FOLDER = "Sent"

# Folders the live cron polls. Backfill wizard targets a single folder.
DEFAULT_LIVE_FOLDERS = ("INBOX", SENT_FOLDER)

# Folders we never poll: trash / drafts / junk produce noise. Les alias
# français sont là parce qu'un serveur peut les servir localisés (Migadu
# sert « Brouillons » à côté de « Drafts » sur la même boîte).
EXCLUDED_FOLDER_PATTERNS = re.compile(
    r"^(Trash|Junk|Drafts|Spam|Corbeille|Brouillons|Ind[ée]sirables|Pourriels)"
    r"(/|$)",
    re.IGNORECASE,
)

# Dossiers exclus de la réconciliation par défaut, en plus du motif dur.
# Motifs ``fnmatch``, insensibles à la casse, surchargeables par l'ICP
# ``bf_email.reconcile_exclude``.
DEFAULT_RECONCILE_EXCLUDE = "Templates,Snoozed,Migadu Backups"

# 🔴 Attributs d'usage spécial que la réconciliation ne relit jamais.
# ``\All`` est le piège coûteux : sur Gmail, `[Gmail]/All Mail` contient
# TOUT le compte — 55 812 messages sur une boîte cliente mesurée le
# 2026-09-20 — et le même message y reparaît sous chacune de ses étiquettes.
# Le dédoublonnage par Message-ID évite les doublons en base, pas le coût :
# la passe tourne aux six heures. ``\Flagged`` et ``\Important`` sont des
# vues de Gmail sur des messages qui vivent ailleurs, jamais un rangement.
EXCLUDED_SPECIAL_USE = {
    "\\all", "\\junk", "\\trash", "\\drafts", "\\flagged", "\\important",
}

# Et son inverse : le dossier des envoyés, quel que soit son nom. Sans lui,
# `DEFAULT_LIVE_FOLDERS` cherche « Sent », que Gmail appelle
# `[Gmail]/Sent Mail` — le journal dit « folder 'Sent' not selectable,
# skipping » et le côté envoyé reste muet, mesuré sur deux comptes réels.
SENT_SPECIAL_USE = "\\sent"


def folder_is_excluded(name, patterns=()):
    """Vrai quand la réconciliation ne doit pas relire ce dossier.

    Deux filtres, et ils ne servent pas la même chose. ``EXCLUDED_FOLDER_``
    ``PATTERNS`` couvre ce qui est du bruit par construction : corbeille,
    brouillons, indésirables. La liste ``patterns`` vient de la configuration
    et couvre ce qui dépend du serveur ou du moment — « Migadu Backups », un
    dossier d'essai « rtqa* » laissé par un banc.

    ⚠️ Ne jamais y mettre un dossier d'archive : c'est précisément là que
    dorment les messages qu'aucune passe n'a captés.
    """
    if not name:
        return True
    if EXCLUDED_FOLDER_PATTERNS.match(name):
        return True
    low = name.lower()
    for pattern in patterns:
        pattern = (pattern or "").strip().lower()
        if pattern and fnmatch.fnmatch(low, pattern):
            return True
    return False


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


def _texte_de_l_erreur(exc):
    """Le message du serveur, lisible par un humain.

    ``str()`` d'une erreur ``imaplib`` rend une chaîne d'OCTETS : le
    ``b'...'`` se retrouvait tel quel dans ``last_error`` et dans la fenêtre
    « Tester la connexion ». Vu en production sur le compte d'André Roy chez
    un locataire réel, en erreur depuis quatre mois. """
    if isinstance(exc, imaplib.IMAP4.error) and exc.args:
        premier = exc.args[0]
        if isinstance(premier, bytes):
            return premier.decode("utf-8", "replace")
        return str(premier)
    return str(exc)


def hote_est_joignable(host):
    """Rend ``(permis, motif)`` pour un hôte de serveur de courriel.

    🔴 Sans cette garde, l'assistant et le bouton « Tester la connexion » sont
    un scanner de réseau interne offert à tout usager interne : les deux
    passent l'hôte ET le port saisis à `open_connection`, et le refus distingue
    un port fermé d'un port ouvert. Mesuré en production le 2026-09-20,
    depuis un compte employé ordinaire :

        <base interne>:5432   -> [SSL: UNEXPECTED_EOF_WHILE_READING]   (ouvert)
        <base interne>:5433   -> [Errno 111] Connection refused        (fermé)
        127.0.0.1:<port web>  -> [SSL: WRONG_VERSION_NUMBER]           (HTTP)

    Le message nomme donc le service. On refuse tout ce qui ne résout pas vers
    une adresse publique ; les hôtes réellement employés en production
    sont tous publics, vérifié avant de poser la garde.
    """
    from .push_transport import _host_is_public  # import local : pas de cycle
    if not host or not isinstance(host, str):
        return False, "aucun serveur"
    if _host_is_public(host):
        return True, ""
    try:
        socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError):
        return False, "introuvable"
    return False, "interne"


def open_connection(host, port, user, password=None, timeout=30, xoauth2=None,
                    autoriser_hote_interne=False):
    """Open an authenticated IMAP4_SSL connection.

    Caller is responsible for ``logout()``. We deliberately do not return
    a context manager - Odoo cron methods commit between batches and we
    keep the connection alive across them.

    ``xoauth2`` : chaîne SASL déjà encodée en base64
    (``bf.email.oauth.chaine_xoauth2``). Quand elle est fournie, le mot de
    passe est ignoré et la session s'authentifie par
    ``AUTHENTICATE XOAUTH2``, seul mécanisme accepté par Microsoft depuis la
    fin de l'authentification de base.
    """
    if not autoriser_hote_interne:
        permis, motif = hote_est_joignable(host)
        if not permis:
            raise ImapConnectionError(
                f"IMAP refusé pour {host} : serveur {motif}. Un serveur de "
                f"courriel doit être joignable sur l'Internet public."
                if motif == "interne" else
                f"IMAP refusé pour {host} : serveur {motif}.")
    try:
        ctx = ssl.create_default_context()
        conn = imaplib.IMAP4_SSL(host, int(port), ssl_context=ctx, timeout=timeout)
        if xoauth2:
            # imaplib encode lui-même en base64 ce que rend le callback, donc
            # on lui redonne les octets bruts de la chaîne SASL.
            brut = base64.b64decode(xoauth2)
            conn.authenticate("XOAUTH2", lambda _defi: brut)
        else:
            conn.login(user, password)
        return conn
    except (imaplib.IMAP4.error, OSError) as exc:
        raise ImapConnectionError(
            f"IMAP login failed for {user} on {host}: {_texte_de_l_erreur(exc)}"
        ) from exc


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



# ---------------------------------------------------------------------------
# En-têtes bruts
# ---------------------------------------------------------------------------
# Le dépliage RFC 5322 (§2.2.3) retire le CRLF et garde le blanc qui le suit.
_FOLD_RE = re.compile(r"\r\n|\r|\n")
# Les octets d'un en-tête 8 bits que le parseur n'a pas su décoder restent
# dans la valeur brute en « surrogateescape » (U+DC80 à U+DCFF).
_SURROGATE_RE = re.compile("[\udc80-\udcff]")
# Pied du bloc d'en-têtes dans un message brut.
_HEADER_END_RE = re.compile(rb"\r?\n\r?\n")
# UTF-8 relu en latin-1 : « Ã¨ » pour « è ».
_MOJIBAKE_RE = re.compile("[ÂÃ][\u0080-¿]")


def unfold_header(value):
    """Valeur d'en-tête sur une seule ligne, sans NUL (refusé par Postgres)."""
    if not value:
        return value or ""
    return _FOLD_RE.sub("", str(value)).replace("\x00", "")


def unfold_display(value):
    """Objet ou expéditeur à afficher : chaque pli devient UNE espace.

    Le dépliage RFC garde le blanc qui suit le saut de ligne, souvent une
    tabulation (« …via SEEK\t<noreply@…> »). Pour un champ affiché, on le
    ramène à une espace.
    """
    if not value:
        return value or ""
    return re.sub(r"[\r\n]+[ \t]*", " ", str(value)).replace("\x00", "")


def _cp1252_fallback(exc):
    """Gestionnaire ``codecs`` : l'octet que l'UTF-8 refuse, relu en cp1252."""
    bad = exc.object[exc.start:exc.end]
    return bad.decode("cp1252", errors="replace"), exc.end


_CP1252_FALLBACK = "bf_email_cp1252_fallback"
codecs.register_error(_CP1252_FALLBACK, _cp1252_fallback)


def _recover_8bit(raw_value):
    """Relire les octets bruts d'un en-tête 8 bits : UTF-8, sinon cp1252.

    Un en-tête doit être en ASCII (RFC 2047 pour le reste). Des expéditeurs
    postent de l'UTF-8 brut, d'autres du latin-1 ; le parseur rend alors
    U+FFFD à la place des lettres. Les octets, eux, sont intacts.
    """
    data = raw_value.encode("ascii", "surrogateescape")
    # Octet par octet : un en-tête peut mêler UTF-8 et latin-1.
    return data.decode("utf-8", errors=_CP1252_FALLBACK)


_UNKNOWN_8BIT_RE = re.compile(
    r"=\?unknown-8bit\?([qb])\?([^?]*)\?=", re.IGNORECASE)
# Séparateurs de ligne Unicode : `splitlines()` coupe dessus, et une valeur
# qui en porte ferait lire une « ligne d'en-tête » de plus dans raw_headers.
_LINE_SEPARATORS_RE = re.compile("[\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029]")
_ANY_SURROGATE_RE = re.compile("[\ud800-\udfff]")


def _reencode_unknown_8bit(raw_value):
    """`=?unknown-8bit?q?Donn=E9es?=` → le même mot, en `=?utf-8?b?…?=`.

    L'original qu'Odoo garde (`as_string()` d'un message lu en politique
    SMTP) écrit ainsi un en-tête 8 bits, que le parseur rend en U+FFFD. On
    relit les octets (UTF-8, sinon cp1252) et on réencode le MOT seul : le
    reste de la valeur passe ensuite par l'analyse normale, adresses comprises.
    """
    def _one(match):
        kind, payload = match.group(1).lower(), match.group(2)
        try:
            if kind == "q":
                data = email.quoprimime.header_decode(payload).encode(
                    "latin-1")
            else:
                data = base64.b64decode(payload + "=" * (-len(payload) % 4))
        except Exception:  # noqa: BLE001 - mot illisible : le laisser
            return match.group(0)
        text = data.decode("utf-8", errors=_CP1252_FALLBACK)
        return "=?utf-8?b?" + base64.b64encode(
            text.encode("utf-8", errors="replace")).decode("ascii") + "?="
    return _UNKNOWN_8BIT_RE.sub(_one, raw_value)


def _clean_value(value):
    """Ce qui ne doit jamais atteindre la base ni couper une ligne."""
    value = _ANY_SURROGATE_RE.sub("�", value)
    value = _LINE_SEPARATORS_RE.sub(" ", value)
    return unfold_header(value).strip()


def header_value(name, raw_value):
    """Valeur décodée (RFC 2047 et 8 bits) et dépliée d'un en-tête brut."""
    raw_value = raw_value if isinstance(raw_value, str) else str(raw_value or "")
    if _SURROGATE_RE.search(raw_value):
        raw_value = _recover_8bit(raw_value)
    if _UNKNOWN_8BIT_RE.search(raw_value):
        raw_value = _reencode_unknown_8bit(raw_value)
    try:
        value = str(email.policy.default.header_fetch_parse(name, raw_value))
    except Exception:  # noqa: BLE001 - en-tête mal formé : garder le texte
        value = raw_value
    # L'original qu'Odoo garde (`as_string()` d'un message lu en politique
    # SMTP) encode un en-tête 8 bits en `=?unknown-8bit?q?Donn=E9es?=` : le
    # décodage rend alors des octets échappés, qu'on relit comme plus haut.
    if _SURROGATE_RE.search(value):
        value = value.encode("utf-8", "surrogateescape").decode(
            "utf-8", errors=_CP1252_FALLBACK)
    # Un en-tête mal formé, ou un mot dans un jeu de caractères piégé
    # (`unicode_escape`), peut garder des surrogates : Postgres les refuse.
    return _clean_value(value)


def headers_text(msg):
    """Bloc ``raw_headers`` d'un message : une ligne ``Nom: valeur`` par en-tête.

    Dans l'ordre du message, doublons compris (``Received``). Ne lit que le
    premier niveau, comme avant. Rend "" plutôt que de lever.
    """
    try:
        return "\n".join(
            "%s: %s" % (name, header_value(name, raw))
            for name, raw in msg.raw_items()
        )
    except Exception:  # noqa: BLE001
        _logger.debug("bf.email : en-têtes illisibles", exc_info=True)
        return ""


def first_header(msg, name):
    """Première valeur décodée de ``name`` dans ``msg``, ou "".

    Comme ``str(msg.get(name, ""))``, mais un en-tête 8 bits en latin-1 garde
    ses lettres au lieu de U+FFFD.
    """
    wanted = name.lower()
    try:
        for key, raw in msg.raw_items():
            if key.lower() == wanted:
                return header_value(key, raw)
    except Exception:  # noqa: BLE001
        _logger.debug("bf.email : en-tête %s illisible", name, exc_info=True)
    return ""


def headers_text_from_bytes(raw_bytes):
    """``headers_text`` d'un message brut, sans analyser le corps."""
    if not raw_bytes:
        return ""
    end = _HEADER_END_RE.search(raw_bytes)
    head = raw_bytes[:end.end()] if end else raw_bytes
    try:
        msg = email.parser.BytesHeaderParser(
            policy=email.policy.default).parsebytes(head)
    except Exception:  # noqa: BLE001
        return ""
    return headers_text(msg)


def header_from_text(headers, name):
    """Première valeur de ``name`` dans un bloc ``raw_headers``, ou ""."""
    prefix = name.lower() + ":"
    # `split("\n")`, pas `splitlines()` : celui-ci coupe aussi sur U+2028.
    for line in (headers or "").split("\n"):
        if line.lower().startswith(prefix):
            return line[len(prefix):].strip()
    return ""


def repair_mojibake(value):
    """« DerniÃ¨re » → « Dernière » ; rend la valeur intacte sinon.

    Ne répare que si la chaîne entière se relit en UTF-8 strict : un vrai
    « Ã » suivi d'un caractère latin-1 n'y survit pas.
    """
    if not value or not _MOJIBAKE_RE.search(value):
        return value
    try:
        return value.encode("cp1252").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        try:
            return value.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            return value


def fetch_full_headers_bulk(conn, uids):
    """``{uid (int): octets de l'en-tête}`` pour des UID du dossier courant.

    ``BODY.PEEK`` : ne pose pas ``\\Seen``. Lecture seule.
    """
    if not uids:
        return {}
    uid_set = ",".join(imap_uid_token(u) for u in uids)
    status, data = conn.uid("FETCH", uid_set, "(UID BODY.PEEK[HEADER])")
    if status != "OK" or not data:
        return {}
    out = {}
    for item in data:
        if not isinstance(item, tuple) or len(item) < 2:
            continue
        meta = item[0].decode("ascii", errors="ignore") if isinstance(
            item[0], bytes) else str(item[0])
        match = re.search(r"UID (\d+)", meta)
        if match and isinstance(item[1], bytes):
            out[int(match.group(1))] = item[1]
    return out


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
            # Attributs d'usage spécial (RFC 6154 et les extensions Gmail).
            # C'est la seule façon de reconnaître le dossier « envoyés » ou
            # la corbeille sur un serveur qui les nomme dans sa langue, ou
            # entre crochets comme Gmail. Les noms en dur ne suffisent pas.
            "special": sorted(
                tok for tok in flags.split()
                if tok.startswith("\\") and tok.lower() not in (
                    "\\haschildren", "\\hasnochildren",
                    "\\noselect", "\\nonexistent", "\\noinferiors",
                    "\\marked", "\\unmarked", "\\subscribed",
                )
            ),
        })
    return out
