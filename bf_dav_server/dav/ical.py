# Part of bf_dav_server. See LICENSE file for full copyright and licensing details.
"""iCalendar reading and writing, independent of the ORM.

Two passes read a client object:

* ``vobject`` gives the meaning of the properties Odoo maps (dates with their
  time zones, rules, participants);
* a raw line pass keeps, byte for byte, everything Odoo does not model
  (Thunderbird ``X-MOZ-*``, Apple ``X-APPLE-*``, ``X-MOZ-GENERATION``,
  attachments, the VALARM blocks as written). A client relies on the server
  to hand those back intact: Thunderbird keeps the state of its reminders in
  the event itself, Apple identifies a reminder by the UID inside its VALARM.
"""

import datetime
import re
from dataclasses import dataclass, field

import pytz

try:
    import vobject
except ImportError:  # pragma: no cover - vobject ships with Odoo
    vobject = None

CRLF = "\r\n"

# Properties of a VEVENT that the server maps to Odoo fields or rebuilds itself.
MAPPED_PROPS = {
    "UID", "DTSTAMP", "CREATED", "LAST-MODIFIED", "SEQUENCE", "SUMMARY",
    "DESCRIPTION", "LOCATION", "DTSTART", "DTEND", "DURATION", "RRULE",
    "EXDATE", "RECURRENCE-ID", "CLASS", "TRANSP", "CATEGORIES", "STATUS",
    "ORGANIZER", "ATTENDEE", "X-CALENDARSERVER-ACCESS", "CONFERENCE",
}
# Per-user reminder state written by Thunderbird on the VEVENT.
MOZ_STATE_PREFIXES = ("X-MOZ-LASTACK", "X-MOZ-SNOOZE-TIME")
# Our own markers, rebuilt from Odoo on every read and ignored on write.
OWN_PREFIX = "X-BF-"

MAX_ICS_BYTES = 1024 * 1024


class IcalError(ValueError):
    """The object cannot be accepted."""

    def __init__(self, message, condition=None):
        super().__init__(message)
        self.condition = condition  # CalDAV precondition element name


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------

def escape_text(value):
    value = (value or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
    return value.replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n")


def fold(line):
    """Fold at 75 octets without cutting a UTF-8 sequence (RFC 5545 §3.1)."""
    raw = line.encode("utf-8")
    if len(raw) <= 75:
        return line
    parts = []
    limit = 75
    while raw:
        cut = min(limit, len(raw))
        while cut < len(raw) and (raw[cut] & 0xC0) == 0x80:
            cut -= 1
        parts.append(raw[:cut].decode("utf-8"))
        raw = raw[cut:]
        limit = 74  # continuation lines start with a space
    return (CRLF + " ").join(parts)


def unfold(text):
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"\n[ \t]", "", text)


def fmt_utc(dt):
    if dt.tzinfo is not None:
        dt = dt.astimezone(pytz.utc).replace(tzinfo=None)
    return dt.strftime("%Y%m%dT%H%M%SZ")


def fmt_date(d):
    return d.strftime("%Y%m%d")


def parse_utc_stamp(value):
    """``20261023T120000Z`` (or without Z, read as UTC) -> naive UTC datetime."""
    value = (value or "").strip()
    m = re.match(r"^(\d{8})T(\d{6})Z?$", value)
    if not m:
        return None
    try:
        return datetime.datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
    except ValueError:
        return None


def to_utc_naive(value, fallback_tz=None):
    """vobject date/datetime -> (naive UTC datetime or date, is_date)."""
    if isinstance(value, datetime.datetime):
        if value.tzinfo is None:
            tz = pytz.timezone(fallback_tz) if fallback_tz else pytz.utc
            value = tz.localize(value)
        return value.astimezone(pytz.utc).replace(tzinfo=None), False
    if isinstance(value, datetime.date):
        return value, True
    raise IcalError("unreadable date")


def prtime(dt):
    """Thunderbird PRTime of a naive UTC datetime: microseconds since epoch."""
    epoch = datetime.datetime(1970, 1, 1)
    return int((dt - epoch).total_seconds()) * 1000000


def from_prtime(value):
    try:
        return datetime.datetime(1970, 1, 1) + datetime.timedelta(microseconds=int(value))
    except (TypeError, ValueError, OverflowError):
        return None


# ---------------------------------------------------------------------------
# Parsed structures
# ---------------------------------------------------------------------------

@dataclass
class Alarm:
    raw: str
    uid: str = ""
    action: str = ""
    minutes_before: int = None   # relative trigger before start, else None
    absolute: datetime.datetime = None
    acknowledged: datetime.datetime = None
    snooze_of: str = ""          # RELATED-TO;RELTYPE=SNOOZE target
    default_alarm: bool = False  # X-APPLE-DEFAULT-ALARM

    @property
    def mappable(self):
        """A reminder Odoo can hold in alarm_ids."""
        # Apple's default reminder is personal to the device: it never becomes
        # a reminder of the meeting for everyone.
        return (self.action in ("DISPLAY", "AUDIO") and self.minutes_before is not None
                and self.minutes_before >= 0 and not self.snooze_of and not self.default_alarm)


@dataclass
class Attendee:
    email: str
    cn: str = ""
    partstat: str = "NEEDS-ACTION"
    schedule_agent: str = ""
    role: str = ""


@dataclass
class Event:
    rid: object = None          # naive UTC datetime or date, None for the master
    allday: bool = False
    start: object = None
    stop: object = None
    summary: str = ""
    description: str = ""
    location: str = ""
    rrule: str = ""
    exdates: list = field(default_factory=list)
    klass: str = None           # None = absent
    transp: str = None
    categories: list = None     # None = absent
    status: str = None
    organizer: str = ""
    organizer_cn: str = ""
    attendees: list = field(default_factory=list)
    alarms: list = field(default_factory=list)
    moz: dict = field(default_factory=dict)
    extra: list = field(default_factory=list)
    tzid: str = ""


@dataclass
class CalendarObject:
    uid: str
    events: list
    vtimezones: list = field(default_factory=list)

    @property
    def master(self):
        for ev in self.events:
            if ev.rid is None:
                return ev
        return None

    @property
    def exceptions(self):
        return [ev for ev in self.events if ev.rid is not None]


# ---------------------------------------------------------------------------
# Raw pass
# ---------------------------------------------------------------------------

def _prop_name(line):
    m = re.match(r"^([A-Za-z0-9-]+)", line)
    return m.group(1).upper() if m else ""


def _raw_components(text):
    """Split the VCALENDAR into VEVENT raw data: list of dicts with
    ``lines`` (unfolded property lines of the VEVENT itself) and
    ``valarms`` (raw text of each VALARM)."""
    lines = unfold(text).split("\n")
    events, vtimezones = [], []
    stack, cur, alarm, tzbuf, other = [], None, None, None, 0
    for line in lines:
        if not line.strip():
            continue
        upper = line.upper()
        if upper.startswith("BEGIN:"):
            comp = upper[6:].strip()
            stack.append(comp)
            if comp == "VEVENT" and len(stack) == 2:
                cur = {"lines": [], "valarms": []}
            elif comp == "VALARM" and cur is not None and stack[-2] == "VEVENT":
                alarm = [line]
            elif comp == "VTIMEZONE" and len(stack) == 2:
                tzbuf = [line]
            elif tzbuf is not None:
                tzbuf.append(line)
            elif alarm is not None:
                alarm.append(line)
            elif cur is not None:
                other += 1
                cur.setdefault("nested", []).append(line)
            elif comp in ("VTODO", "VJOURNAL", "VFREEBUSY") and len(stack) == 2:
                raise IcalError("only VEVENT is supported", "supported-calendar-component")
            continue
        if upper.startswith("END:"):
            comp = upper[4:].strip()
            if not stack:
                raise IcalError("unbalanced END")
            stack.pop()
            if comp == "VALARM" and alarm is not None:
                alarm.append(line)
                cur["valarms"].append(CRLF.join(alarm) + CRLF)
                alarm = None
            elif comp == "VEVENT" and cur is not None and len(stack) == 1:
                events.append(cur)
                cur = None
            elif comp == "VTIMEZONE" and tzbuf is not None and len(stack) == 1:
                tzbuf.append(line)
                vtimezones.append(CRLF.join(tzbuf) + CRLF)
                tzbuf = None
            elif tzbuf is not None:
                tzbuf.append(line)
            elif cur is not None and "nested" in cur:
                cur["nested"].append(line)
            continue
        if tzbuf is not None:
            tzbuf.append(line)
        elif alarm is not None:
            alarm.append(line)
        elif cur is not None and stack and stack[-1] == "VEVENT":
            cur["lines"].append(line)
        elif cur is not None:
            cur.setdefault("nested", []).append(line)
    if stack:
        raise IcalError("unterminated component")
    return events, vtimezones


def _param(line, name):
    head = line.split(":", 1)[0]
    for part in head.split(";")[1:]:
        if "=" in part:
            key, val = part.split("=", 1)
            if key.strip().upper() == name:
                return val.strip().strip('"')
    return ""


def _value(line):
    # The value starts after the first colon that is not inside a quoted parameter.
    in_quote = False
    for i, ch in enumerate(line):
        if ch == '"':
            in_quote = not in_quote
        elif ch == ":" and not in_quote:
            return line[i + 1:]
    return ""


_DUR = re.compile(r"^([+-])?P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$")


def parse_duration(value):
    m = _DUR.match((value or "").strip().upper())
    if not m:
        return None
    sign, w, d, h, mi, s = m.groups()
    delta = datetime.timedelta(weeks=int(w or 0), days=int(d or 0), hours=int(h or 0),
                               minutes=int(mi or 0), seconds=int(s or 0))
    return -delta if sign == "-" else delta


def parse_alarm(raw):
    alarm = Alarm(raw=raw)
    for line in unfold(raw).split("\n"):
        name = _prop_name(line)
        value = _value(line).strip()
        if name == "UID":
            alarm.uid = value
        elif name == "ACTION":
            alarm.action = value.upper()
        elif name == "TRIGGER":
            if _param(line, "VALUE").upper() == "DATE-TIME":
                alarm.absolute = parse_utc_stamp(value)
            else:
                delta = parse_duration(value)
                related_end = _param(line, "RELATED").upper() == "END"
                if delta is not None and not related_end and delta <= datetime.timedelta(0):
                    alarm.minutes_before = int(-delta.total_seconds() // 60)
        elif name == "ACKNOWLEDGED":
            alarm.acknowledged = parse_utc_stamp(value)
        elif name == "RELATED-TO" and _param(line, "RELTYPE").upper() == "SNOOZE":
            alarm.snooze_of = value
        elif name == "X-APPLE-DEFAULT-ALARM":
            alarm.default_alarm = value.upper() == "TRUE"
        elif name == "X-WR-ALARMUID" and not alarm.uid:
            alarm.uid = value
    return alarm


# ---------------------------------------------------------------------------
# Meaning pass (vobject)
# ---------------------------------------------------------------------------

def _mailto(value):
    value = (value or "").strip()
    if value.lower().startswith("mailto:"):
        value = value[7:]
    return value.strip().lower()


def _first(comp, name):
    lst = comp.contents.get(name.lower())
    return lst[0] if lst else None


def _text(comp, name):
    line = _first(comp, name)
    if line is None:
        return None
    value = line.value
    if isinstance(value, list):
        value = ",".join(str(v) for v in value)
    return str(value or "")


def parse_calendar(text, default_tz="UTC"):
    """Read a client calendar object (one UID, VEVENTs only)."""
    if vobject is None:  # pragma: no cover
        raise IcalError("vobject missing")
    if isinstance(text, bytes):
        if len(text) > MAX_ICS_BYTES:
            raise IcalError("object too large", "max-resource-size")
        text = text.decode("utf-8")
    if len(text.encode("utf-8")) > MAX_ICS_BYTES:
        raise IcalError("object too large", "max-resource-size")
    raw_events, vtimezones = _raw_components(text)
    if not raw_events:
        raise IcalError("no VEVENT", "supported-calendar-component")
    try:
        cal = vobject.readOne(text, transform=True)
    except Exception as exc:  # vobject raises many kinds
        raise IcalError("unreadable iCalendar: %s" % exc, "valid-calendar-data") from exc
    vevents = list(getattr(cal, "vevent_list", []))
    if len(vevents) != len(raw_events):
        raise IcalError("component mismatch", "valid-calendar-data")

    uids = set()
    events = []
    for vev, raw in zip(vevents, raw_events):
        uid = _text(vev, "UID")
        if not uid:
            raise IcalError("VEVENT without UID", "valid-calendar-data")
        uids.add(uid.strip())
        ev = Event()
        dtstart = _first(vev, "DTSTART")
        if dtstart is None:
            raise IcalError("VEVENT without DTSTART", "valid-calendar-data")
        # vobject drops the TZID parameter once it has turned the value into
        # an aware datetime: read the zone name from the raw line instead.
        raw_start = next((ln for ln in raw["lines"] if _prop_name(ln) == "DTSTART"), "")
        ev.tzid = _param(raw_start, "TZID") or (dtstart.params.get("TZID") or [""])[0]
        if not ev.tzid and _value(raw_start).strip().upper().endswith("Z"):
            ev.tzid = "UTC"
        elif not ev.tzid and isinstance(dtstart.value, datetime.datetime) and dtstart.value.tzinfo is not None:
            zone = getattr(dtstart.value.tzinfo, "zone", "") or getattr(dtstart.value.tzinfo, "key", "")
            ev.tzid = zone if zone and zone != "UTC" else ""
        # A TZID that vobject could not resolve leaves a naive time: read it in
        # that zone when pytz knows the name, else in the viewer's zone.
        local_tz = ev.tzid if ev.tzid in pytz.all_timezones_set else default_tz
        ev.start, ev.allday = to_utc_naive(dtstart.value, local_tz)
        dtend = _first(vev, "DTEND")
        duration = _first(vev, "DURATION")
        if dtend is not None:
            ev.stop, _ = to_utc_naive(dtend.value, local_tz)
        elif duration is not None:
            ev.stop = ev.start + duration.value
        else:
            ev.stop = ev.start + (datetime.timedelta(days=1) if ev.allday else datetime.timedelta(0))
        if ev.allday and isinstance(ev.stop, datetime.datetime):
            ev.stop = ev.stop.date()
        rid = _first(vev, "RECURRENCE-ID")
        if rid is not None:
            ev.rid, _ = to_utc_naive(rid.value, local_tz)
        ev.summary = _text(vev, "SUMMARY") or ""
        ev.description = _text(vev, "DESCRIPTION") or ""
        ev.location = _text(vev, "LOCATION") or ""
        rrule = _first(vev, "RRULE")
        if rrule is not None:
            ev.rrule = str(rrule.value).strip()
        for line in vev.contents.get("exdate", []):
            for value in (line.value if isinstance(line.value, list) else [line.value]):
                ev.exdates.append(to_utc_naive(value, local_tz)[0])
        klass = _text(vev, "CLASS")
        ev.klass = klass.strip().upper() if klass is not None else None
        access = _text(vev, "X-CALENDARSERVER-ACCESS")
        if ev.klass is None and access:
            ev.klass = {"PUBLIC": "PUBLIC", "PRIVATE": "PRIVATE",
                        "CONFIDENTIAL": "CONFIDENTIAL", "RESTRICTED": "PRIVATE"}.get(
                access.strip().upper())
        transp = _text(vev, "TRANSP")
        ev.transp = transp.strip().upper() if transp is not None else None
        if "categories" in vev.contents:
            cats = []
            for line in vev.contents["categories"]:
                vals = line.value if isinstance(line.value, list) else str(line.value).split(",")
                cats += [str(v).strip() for v in vals if str(v).strip()]
            ev.categories = cats
        status = _text(vev, "STATUS")
        ev.status = status.strip().upper() if status else None
        org = _first(vev, "ORGANIZER")
        if org is not None:
            ev.organizer = _mailto(org.value)
            ev.organizer_cn = (org.params.get("CN") or [""])[0]
        for line in vev.contents.get("attendee", []):
            email = _mailto(line.value)
            if not email:
                continue
            ev.attendees.append(Attendee(
                email=email,
                cn=(line.params.get("CN") or [""])[0],
                partstat=(line.params.get("PARTSTAT") or ["NEEDS-ACTION"])[0].upper(),
                schedule_agent=(line.params.get("SCHEDULE-AGENT") or [""])[0].upper(),
                role=(line.params.get("ROLE") or [""])[0].upper(),
            ))
        for line in raw["lines"]:
            name = _prop_name(line)
            if name.startswith(MOZ_STATE_PREFIXES):
                ev.moz[name] = _value(line).strip()
            elif name.startswith(OWN_PREFIX) or name in MAPPED_PROPS:
                continue
            else:
                ev.extra.append(line)
        if raw.get("nested"):
            ev.extra += raw["nested"]
        ev.alarms = [parse_alarm(a) for a in raw["valarms"]]
        events.append(ev)

    if len(uids) != 1:
        raise IcalError("several UIDs in one object", "valid-calendar-object-resource")
    masters = [ev for ev in events if ev.rid is None]
    if len(masters) > 1:
        raise IcalError("several masters", "valid-calendar-object-resource")
    return CalendarObject(uid=uids.pop(), events=events, vtimezones=vtimezones)


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

_VTZ_CACHE = {}


def vtimezone_lines(tzname):
    if tzname in _VTZ_CACHE:
        return list(_VTZ_CACHE[tzname] or [])
    lines = None
    try:
        block = vobject.icalendar.TimezoneComponent(tzinfo=pytz.timezone(tzname)).serialize()
        lines = [ln for ln in unfold(block).split("\n") if ln]
        if not lines or not lines[0].upper().startswith("BEGIN:VTIMEZONE"):
            lines = None
    except Exception:  # unknown zone
        lines = None
    _VTZ_CACHE[tzname] = lines
    return list(lines or [])


@dataclass
class OutEvent:
    """Everything the writer needs for one VEVENT, already viewer-specific."""
    uid: str
    start: object
    stop: object
    allday: bool = False
    rid: object = None
    summary: str = ""
    description: str = ""
    location: str = ""
    rrule: str = ""
    exdates: list = field(default_factory=list)
    klass: str = ""
    transp: str = "OPAQUE"
    categories: list = field(default_factory=list)
    status: str = ""
    sequence: int = None
    organizer: tuple = None          # (email, cn)
    attendees: list = field(default_factory=list)   # (email, cn, partstat)
    valarms: list = field(default_factory=list)      # raw VALARM blocks
    moz: dict = field(default_factory=dict)
    extra: list = field(default_factory=list)
    own: list = field(default_factory=list)          # X-BF-* lines
    created: datetime.datetime = None
    modified: datetime.datetime = None


def _date_lines(prop, value, allday, tzname):
    if allday:
        return ["%s;VALUE=DATE:%s" % (prop, fmt_date(value))]
    if tzname and value.year < 2037:
        local = pytz.utc.localize(value).astimezone(pytz.timezone(tzname))
        return ["%s;TZID=%s:%s" % (prop, tzname, local.strftime("%Y%m%dT%H%M%S"))]
    return ["%s:%s" % (prop, fmt_utc(value))]


def _rrule_line(rrule, tzname):
    rule = rrule.strip()
    if "\n" in rule:
        rule = [ln for ln in rule.splitlines() if ln.upper().startswith("RRULE")][-1:] or [rule]
        rule = rule[0]
    if rule.upper().startswith("RRULE:"):
        rule = rule[6:]
    # UNTIL must be UTC when DTSTART carries a TZID (RFC 5545 §3.3.10).
    def fix_until(m):
        value = m.group(1)
        if value.endswith("Z") or "T" not in value:
            return "UNTIL=" + value
        try:
            local = datetime.datetime.strptime(value, "%Y%m%dT%H%M%S")
        except ValueError:
            return "UNTIL=" + value
        if tzname:
            local = pytz.timezone(tzname).localize(local)
            return "UNTIL=" + fmt_utc(local)
        return "UNTIL=" + value + "Z"
    rule = re.sub(r"UNTIL=([0-9TZ]+)", fix_until, rule)
    return "RRULE:" + rule


def _event_lines(ev, tzname, stamp):
    lines = ["BEGIN:VEVENT", "UID:%s" % ev.uid, "DTSTAMP:%s" % fmt_utc(stamp)]
    if ev.created:
        lines.append("CREATED:%s" % fmt_utc(ev.created))
    if ev.modified:
        lines.append("LAST-MODIFIED:%s" % fmt_utc(ev.modified))
    if ev.sequence is not None:
        lines.append("SEQUENCE:%d" % ev.sequence)
    if ev.rid is not None:
        if isinstance(ev.rid, datetime.datetime) and not ev.allday:
            # In UTC: an instant, whatever the zone rules a client holds (the
            # VTIMEZONE built from pytz stops at 2037).
            lines.append("RECURRENCE-ID:%s" % fmt_utc(ev.rid))
        else:
            rid = ev.rid.date() if isinstance(ev.rid, datetime.datetime) else ev.rid
            lines.append("RECURRENCE-ID;VALUE=DATE:%s" % fmt_date(rid))
    lines.append("SUMMARY:%s" % escape_text(ev.summary))
    lines += _date_lines("DTSTART", ev.start, ev.allday, tzname)
    lines += _date_lines("DTEND", ev.stop, ev.allday, tzname)
    if ev.rrule:
        lines.append(_rrule_line(ev.rrule, None if ev.allday else tzname))
    for ex in ev.exdates:
        if ev.allday:
            d = ex.date() if isinstance(ex, datetime.datetime) else ex
            lines.append("EXDATE;VALUE=DATE:%s" % fmt_date(d))
        else:
            lines.append("EXDATE:%s" % fmt_utc(ex))
    if ev.location:
        lines.append("LOCATION:%s" % escape_text(ev.location))
    if ev.description:
        lines.append("DESCRIPTION:%s" % escape_text(ev.description))
    lines.append("TRANSP:%s" % (ev.transp or "OPAQUE"))
    if ev.klass:
        lines.append("CLASS:%s" % ev.klass)
        lines.append("X-CALENDARSERVER-ACCESS:%s" % ev.klass)
    if ev.categories:
        lines.append("CATEGORIES:%s" % ",".join(escape_text(c) for c in ev.categories))
    if ev.status:
        lines.append("STATUS:%s" % ev.status)
    if ev.organizer and ev.organizer[0]:
        cn = (ev.organizer[1] or "").replace('"', "'")
        lines.append('ORGANIZER;CN="%s":mailto:%s' % (cn, ev.organizer[0]))
    for email, cn, partstat in ev.attendees:
        cn = (cn or "").replace('"', "'")
        rsvp = ";RSVP=TRUE" if partstat == "NEEDS-ACTION" and not (
            ev.organizer and email == ev.organizer[0]) else ""
        lines.append('ATTENDEE;CN="%s";CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=%s%s:mailto:%s'
                     % (cn, partstat, rsvp, email))
    for name in sorted(ev.moz):
        lines.append("%s:%s" % (name, ev.moz[name]))
    lines += ev.own
    lines += ev.extra
    for block in ev.valarms:
        lines += [ln for ln in unfold(block).split("\n") if ln]
    lines.append("END:VEVENT")
    return lines


def build_calendar(events, tzname=None, stamp=None, prodid="-//Symbifox//bf_dav_server//EN"):
    """Serialize OutEvents sharing one UID into a VCALENDAR (CRLF, folded)."""
    stamp = stamp or datetime.datetime(2000, 1, 1)
    tzlines = vtimezone_lines(tzname) if tzname and tzname != "UTC" else []
    if tzname and not tzlines:
        tzname = None
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:%s" % prodid, "CALSCALE:GREGORIAN"]
    lines += tzlines
    for ev in events:
        lines += _event_lines(ev, tzname if not ev.allday else None, stamp)
    lines.append("END:VCALENDAR")
    return CRLF.join(fold(ln) for ln in lines) + CRLF


def valarm_block(uid, minutes, description, acknowledged=None, extra=()):
    lines = [
        "BEGIN:VALARM",
        "UID:%s" % uid,
        "X-WR-ALARMUID:%s" % uid,
        "ACTION:DISPLAY",
        "TRIGGER;RELATED=START:-PT%dM" % int(minutes),
        "DESCRIPTION:%s" % escape_text(description or "Reminder"),
    ]
    if acknowledged:
        lines.append("ACKNOWLEDGED:%s" % fmt_utc(acknowledged))
    lines += list(extra)
    lines.append("END:VALARM")
    return CRLF.join(lines) + CRLF


def set_acknowledged(raw, acknowledged):
    """Return a VALARM block with ACKNOWLEDGED set to ``acknowledged``."""
    lines = [ln for ln in unfold(raw).split("\n") if ln and _prop_name(ln) != "ACKNOWLEDGED"]
    if acknowledged:
        lines.insert(len(lines) - 1, "ACKNOWLEDGED:%s" % fmt_utc(acknowledged))
    return CRLF.join(lines) + CRLF
