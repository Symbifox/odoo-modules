"""iMIP (calendar invitation) extraction for bf_email_management.

Parses ``text/calendar`` MIME parts (RFC 5545 / iMIP RFC 6047) carried by an
incoming email into normalized dicts that the ingest layer turns into
*tentative* ``calendar.event`` rows.

We use ``vobject`` (bundled with Odoo) rather than a hand-rolled line parser
because real invitations from Microsoft/Teams and Google routinely use:

* folded lines (a leading space continues the previous line — e.g. the UID),
* property parameters (``SUMMARY;LANGUAGE=fr-FR:...``),
* embedded custom ``VTIMEZONE`` blocks named e.g. ``Eastern Standard Time``
  (NOT an IANA zone — ``pytz.timezone("Eastern Standard Time")`` would raise),

all of which vobject resolves correctly (validated against a live Teams
invite: ``DTSTART;TZID=Eastern Standard Time:20260709T110000`` → 15:00 UTC).

Nothing here touches the ORM; ``bf.email._maybe_ingest_calendar_invite``
consumes the dicts. Every entry point is defensive: a malformed calendar part
must never interrupt email ingestion.
"""

import base64
import binascii
import logging
import re
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

_logger = logging.getLogger(__name__)

# Only these iMIP methods describe an invitation we should materialize.
# COUNTER (propose new time) and PUBLISH (broadcast feeds) are intentionally
# ignored by the caller. REPLY is not an invitation: it is a guest answering
# one the mailbox owner ORGANIZES, and it takes its own path
# (``bf.email._imip_apply_reply``), which never creates anything.
ACTIONABLE_METHODS = ("REQUEST", "CANCEL")

# RFC 5545 §3.2.12 PARTSTAT → ``calendar.attendee.state``. DELEGATED and the
# VTODO-only values have no counterpart and are left alone.
REPLY_PARTSTATS = {
    "ACCEPTED": "accepted",
    "TENTATIVE": "tentative",
    "DECLINED": "declined",
    "NEEDS-ACTION": "needsAction",
}

# Les hôtes chez qui une adresse de visioconférence est une SALLE. La liste
# ne vaut que par la façon dont on la lit : voir `_est_une_salle_de_visio`.
_VISIO_HOTES = (
    "teams.microsoft.com", "teams.live.com", "meet.google.com", "zoom.us",
    "webex.com", "whereby.com", "meet.jit.si", "chime.aws", "8x8.vc",
    "gotomeeting.com", "bluejeans.com",
)

_URL_RE = re.compile(r"""https?://[^\s<>"']+""", re.IGNORECASE)


def _est_une_salle_de_visio(adresse):
    """L'adresse est-elle une salle chez un fournisseur reconnu ?

    🔴 L'hôte est ANALYSÉ, puis comparé par égalité ou par vrai sous-domaine.
    La version d'avant cherchait la liste en SOUS-CHAÎNE dans l'URL entière,
    et son commentaire prétendait déjà faire mieux : un hôte connu posé dans
    la REQUÊTE (`https://phishing.test/go?r=https://meet.google.com/x`) ou
    dans le CHEMIN (`https://phishing.test/meet.google.com/abc`) suffisait
    alors à décrocher un bouton « Rejoindre » et un lien cliquable vers le
    site de l'inconnu qui a écrit l'invitation.

    Trois choses que seule l'analyse donne :

    * `urlsplit().hostname` écarte le `userinfo` — l'hôte de
      `https://meet.google.com@phishing.test/x` est `phishing.test` ;
    * `endswith("." + hote)` et non `hote in` : un vrai sous-domaine
      (`us02web.zoom.us`, `societe.webex.com`) passe, `monzoom.us.ailleurs.test`
      non ;
    * le schéma est vérifié : `startswith("http")` admettait `httpx://`, et
      `javascript:` n'a pas d'hôte mais reste cliquable.
    """
    if not adresse:
        return False
    try:
        morceaux = urlsplit(adresse.strip())
    except ValueError:
        return False
    if morceaux.scheme.lower() not in ("http", "https"):
        return False
    hote = (morceaux.hostname or "").lower().rstrip(".")
    if not hote:
        return False
    return any(hote == connu or hote.endswith("." + connu)
               for connu in _VISIO_HOTES)


def _conference_url(vevent, location, description):
    """L'adresse de visioconférence que porte le VEVENT, ou "".

    Trois sources, dans l'ordre où elles sont fiables : les propriétés que les
    fournisseurs posent exprès, l'`URL` du VEVENT quand c'en est une, puis une
    adresse reconnue dans le lieu ou la description — c'est là que Teams et
    Meet la mettent vraiment (mesuré sur une base réelle : la grande majorité des
    invitations externes portent un lien, presque toutes dans la description).

    🔴 La liste d'hôtes s'applique aux TROIS, et pas seulement à la dernière.
    Le `.ics` vient d'un inconnu : `X-GOOGLE-CONFERENCE` et `URL` sont des
    champs comme les autres, que rien n'empêche de porter l'adresse de son
    choix, et ce qu'on en fait est un bouton « Rejoindre » plus un lien
    cliquable dans la description.

    ⚠️ Conséquence assumée : une salle chez un fournisseur hors liste (un
    Jitsi auto-hébergé, par exemple) n'est plus retenue même quand elle arrive
    par `URL`. Le lien reste lisible dans le texte de l'organisateur, qui est
    rendu tel quel ; c'est le champ `videocall_location` et son bouton qui
    exigent un hôte connu.
    """
    for nom in ("x-google-conference", "x-microsoft-skypeteamsmeetingurl"):
        for ligne in vevent.contents.get(nom, []):
            valeur = str(getattr(ligne, "value", "") or "").strip()
            if _est_une_salle_de_visio(valeur):
                return valeur
    for ligne in vevent.contents.get("url", []):
        valeur = str(getattr(ligne, "value", "") or "").strip()
        if _est_une_salle_de_visio(valeur):
            return valeur
    for texte in (location or "", description or ""):
        for trouve in _URL_RE.findall(texte):
            adresse = trouve.rstrip(".,;)>]")
            if _est_une_salle_de_visio(adresse):
                return adresse
    return ""


# Google rewrites the UID of an invitation it received from elsewhere once the
# series is split on its side: ``_<base32hex(original UID)>_R<start>@google.com``.
# Measured on a real mailbox: about a third of the answers, all on recurring
# meetings, carried such a UID; decoded, each one is the UID the Odoo series
# holds.
_GOOGLE_REWRITTEN_UID = re.compile(
    r"^_(?P<b32>[0-9a-v]+)(?:_R(?P<split>\d{8}T\d{6}Z?))?@google\.com$",
    re.IGNORECASE,
)


def _iter_calendar_payloads(msg):
    """Yield the decoded text of every ``text/calendar`` part in ``msg``."""
    for part in msg.walk():
        if part.get_content_type() != "text/calendar":
            continue
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        charset = part.get_content_charset() or "utf-8"
        try:
            yield payload.decode(charset, errors="replace")
        except (LookupError, UnicodeDecodeError):
            yield payload.decode("utf-8", errors="replace")


def calendar_payload_for_uid(msg, uid):
    """La partie ``text/calendar`` qui porte VRAIMENT cet UID, ou "".

    Le fichier reçu est gardé en pièce sur l'événement : la source
    exacte — fuseau, SEQUENCE, X-propriétés — ne se reconstruit pas depuis les
    champs qu'on a retenus.

    ⚠️ Un message peut porter plusieurs parties calendrier, et l'ingestion
    traite un VEVENT à la fois : garder « la première » attachait, sur
    l'événement de la seconde invitation, le fichier de la première. Le
    rapprochement se fait sur l'UID, lu par vobject plutôt qu'au motif, parce
    qu'un UID se replie sur plusieurs lignes aussi volontiers que les autres
    propriétés.

    Faute de vobject ou de correspondance, on rend la SEULE partie quand il
    n'y en a qu'une, et "" quand il y en a plusieurs : attacher le mauvais
    fichier est pire que n'en attacher aucun.
    """
    parties = list(_iter_calendar_payloads(msg))
    if not parties:
        return ""
    cible = (uid or "").strip()
    if cible:
        try:
            import vobject
        except Exception:  # noqa: BLE001  # pragma: no cover
            vobject = None
        if vobject is not None:
            for texte in parties:
                try:
                    for cal in vobject.readComponents(
                            texte, ignoreUnreadable=True):
                        for vevent in cal.contents.get("vevent", []):
                            prop = _get(vevent, "uid")
                            valeur = (prop.value or "").strip() if prop else ""
                            if valeur == cible:
                                return texte
                except Exception as exc:  # noqa: BLE001
                    _logger.warning(
                        "bf_email iMIP: partie calendrier illisible au "
                        "rapprochement d'UID : %s", exc,
                    )
                    continue
    return parties[0] if len(parties) == 1 else ""


def message_has_calendar_part(msg):
    """Cheap pre-check: does the message carry any text/calendar part?"""
    for part in msg.walk():
        if part.get_content_type() == "text/calendar":
            return True
    return False


def _to_odoo_datetime(value):
    """Return ``(odoo_string, allday_bool)`` for a vobject date/datetime.

    Datetimes are normalized to UTC-naive (Odoo's storage convention). A bare
    ``date`` (VALUE=DATE) yields an all-day marker.
    """
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        return value.strftime("%Y-%m-%d %H:%M:%S"), False
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d"), True
    return None, False


def _default_stop(start_str, allday):
    """Derive a stop when the VEVENT omits DTEND (1h, or +1 day all-day)."""
    try:
        if allday:
            d = datetime.strptime(start_str, "%Y-%m-%d")
            return (d + timedelta(days=1)).strftime("%Y-%m-%d")
        d = datetime.strptime(start_str, "%Y-%m-%d %H:%M:%S")
        return (d + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return start_str


def _addr(value):
    """Normalize an ORGANIZER/ATTENDEE value to a bare lowercase address."""
    if not value:
        return ""
    text = str(value).strip()
    if text.lower().startswith("mailto:"):
        text = text[7:]
    return text.strip().lower()


def _get(component, name):
    """Return the first sub-component/property ``name`` or None."""
    try:
        return getattr(component, name)
    except AttributeError:
        return None


def parse_imip_events(msg):
    """Parse every VEVENT across the message's text/calendar parts.

    Returns a list of dicts (possibly empty). Never raises — parse failures
    are logged and skipped so IMAP ingestion is never interrupted.
    """
    if not message_has_calendar_part(msg):
        return []
    try:
        import vobject
    except Exception:  # pragma: no cover - vobject ships with Odoo
        _logger.warning(
            "bf_email iMIP: vobject unavailable — skipping calendar parse"
        )
        return []

    events = []
    for text in _iter_calendar_payloads(msg):
        try:
            for cal in vobject.readComponents(text, ignoreUnreadable=True):
                method_prop = _get(cal, "method")
                method = (
                    method_prop.value.upper()
                    if method_prop and method_prop.value
                    else None
                )
                for vevent in cal.contents.get("vevent", []):
                    parsed = _parse_vevent(vevent, method)
                    if parsed:
                        events.append(parsed)
        except Exception as exc:  # noqa: BLE001 - never break ingestion
            _logger.warning(
                "bf_email iMIP: failed to parse a calendar part: %s", exc
            )
            continue
    return events


def _parse_vevent(vevent, method):
    """Normalize one VEVENT into a plain dict, or None if unusable."""
    uid_prop = _get(vevent, "uid")
    uid = uid_prop.value.strip() if uid_prop and uid_prop.value else None
    if not uid:
        return None

    dtstart = _get(vevent, "dtstart")
    if not dtstart or dtstart.value is None:
        return None
    start_str, allday = _to_odoo_datetime(dtstart.value)
    if not start_str:
        return None

    dtend = _get(vevent, "dtend")
    if dtend and dtend.value is not None:
        stop_str, _allday_end = _to_odoo_datetime(dtend.value)
    else:
        stop_str = _default_stop(start_str, allday)

    def _val(name, default=""):
        prop = _get(vevent, name)
        return prop.value.strip() if prop and prop.value else default

    organizer_prop = _get(vevent, "organizer")
    organizer = _addr(organizer_prop.value) if organizer_prop else ""
    organizer_cn = ""
    if organizer_prop is not None:
        params = getattr(organizer_prop, "params", None) or {}
        organizer_cn = str((params.get("CN") or [""])[0] or "").strip()

    attendees = []
    partstats = {}
    # Les invités, avec de quoi les NOMMER. `attendees` reste la
    # liste d'adresses nues que lisent les gardes de l'ingestion, et ne change
    # ni de forme ni d'ordre ; `invites` porte en plus le nom affiché, le rôle
    # et la réponse, pour que l'événement puisse les montrer.
    invites = []
    for att in vevent.contents.get("attendee", []):
        addr = _addr(att.value)
        if addr and addr not in attendees:
            attendees.append(addr)
        params = (getattr(att, "params", None) or {})
        values = params.get("PARTSTAT") or []
        if addr and values:
            partstats[addr] = str(values[0]).strip().upper()
        if not addr or any(inv["email"] == addr for inv in invites):
            continue
        invites.append({
            "email": addr,
            "cn": str((params.get("CN") or [""])[0] or "").strip(),
            "role": str((params.get("ROLE") or [""])[0] or "").strip().upper(),
            "partstat": str((values or [""])[0] or "").strip().upper(),
        })

    sequence = 0
    seq_prop = _get(vevent, "sequence")
    if seq_prop is not None and seq_prop.value is not None:
        try:
            sequence = int(str(seq_prop.value).strip())
        except (TypeError, ValueError):
            sequence = 0

    recurrence_id = None
    rid_prop = _get(vevent, "recurrence_id")
    if rid_prop is not None and rid_prop.value is not None:
        recurrence_id, _rid_allday = _to_odoo_datetime(rid_prop.value)

    dtstamp = None
    stamp_prop = _get(vevent, "dtstamp")
    if stamp_prop is not None and stamp_prop.value is not None:
        dtstamp, _stamp_allday = _to_odoo_datetime(stamp_prop.value)

    status_prop = _get(vevent, "status")
    status = status_prop.value.upper() if status_prop and status_prop.value else ""

    return {
        "uid": uid,
        "method": method or "REQUEST",
        "summary": _val("summary") or "(sans titre)",
        "start": start_str,
        "stop": stop_str,
        "allday": allday,
        "location": _val("location"),
        "description": _val("description"),
        "status": status,
        "organizer": organizer,
        "attendees": attendees,
        # Only meaningful on a REPLY: what each listed guest answered.
        "partstats": partstats,
        # Odoo-formatted start of the ONE occurrence a REPLY is about, or None
        # when it answers for the whole event or series.
        "recurrence_id": recurrence_id,
        "dtstamp": dtstamp,
        # Ce que l'invitation disait et qu'on jetait.
        "invites": invites,
        "organizer_cn": organizer_cn,
        "conference": _conference_url(
            vevent, _val("location"), _val("description")),
        "sequence": sequence,
    }


def dmarc_failed(msg):
    """True when OUR receiving server recorded an explicit DMARC failure.

    Only the TOPMOST ``Authentication-Results`` header counts: it is the one
    the receiving MX added last. Anything below it travelled with the message
    and can be forged. No header, ``dmarc=none`` (the sender's domain publishes
    no policy) or ``dmarc=pass`` is not a failure.

    Measured on a real mailbox: current mail from every guest passes; the
    only failures came from one sender domain, before it fixed its DKIM.
    """
    try:
        headers = msg.get_all("Authentication-Results") or []
        if not headers:
            return False
        return bool(re.search(r"\bdmarc=fail\b", str(headers[0]), re.IGNORECASE))
    except Exception:  # noqa: BLE001 - an unreadable verdict is not a pass
        _logger.warning("bf_email iMIP: en-tête Authentication-Results illisible")
        return True


def received_at(msg):
    """The message's own ``Date``, as a naive UTC datetime, or None.

    Used to cap a guest's ``DTSTAMP``: a client clock running ahead (or a
    floating local time read as UTC) must not stamp an answer in the future,
    where it would outrank every real answer that follows.
    """
    try:
        value = msg.get("Date")
        if not value:
            return None
        stamp = parsedate_to_datetime(str(value))
    except Exception:  # noqa: BLE001
        return None
    if stamp is None:
        return None
    if stamp.tzinfo is not None:
        stamp = stamp.astimezone(timezone.utc).replace(tzinfo=None)
    return stamp


def google_source_uid(uid):
    """Undo Google's rewrite of a foreign UID, if ``uid`` is one.

    Returns ``(original_uid, split_start)``: the UID the organizer's calendar
    minted, and the Odoo-formatted start of the split-off series (None when
    the UID carries no ``_R`` suffix). ``(None, None)`` for any other UID,
    including a Google UID that does not decode to readable text.
    """
    match = _GOOGLE_REWRITTEN_UID.match((uid or "").strip())
    if not match:
        return None, None
    b32 = match.group("b32").upper()
    b32 += "=" * (-len(b32) % 8)
    try:
        original = base64.b32hexdecode(b32).decode("utf-8")
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return None, None
    if not original or not original.isprintable():
        return None, None
    split_start = None
    if match.group("split"):
        try:
            split_start = datetime.strptime(
                match.group("split")[:15], "%Y%m%dT%H%M%S"
            ).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            split_start = None
    return original, split_start
