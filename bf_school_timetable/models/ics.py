"""A minimal iCalendar writer: enough for a family to subscribe to a child's timetable.

RFC 5545: lines end with CRLF and fold at 75 octets, text escapes the backslash, the semicolon,
the comma and the newline, times are written in UTC so no calendar program has to guess a zone.
"""
from datetime import datetime, timezone

PRODID = "-//Symbifox//School timetable//EN"


def escape(text):
    return (str(text or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
            .replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n"))


def fold(line):
    """Fold at 75 octets without cutting a multi-byte character."""
    raw = line.encode()
    if len(raw) <= 75:
        return line
    parts, current, size = [], "", 0
    for char in line:
        width = len(char.encode())
        if size + width > (75 if not parts else 74):
            parts.append(current)
            current, size = "", 0
        current += char
        size += width
    parts.append(current)
    return "\r\n ".join(parts)


def utc(value):
    return value.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def build_ics(name, events, now=None):
    """`events`: dicts with uid, start, end (aware datetimes), summary, location, description."""
    now = now or datetime.now(timezone.utc)
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:" + PRODID, "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
             "X-WR-CALNAME:" + escape(name), "REFRESH-INTERVAL;VALUE=DURATION:PT12H", "X-PUBLISHED-TTL:PT12H"]
    for event in events:
        lines += ["BEGIN:VEVENT", "UID:" + event["uid"], "DTSTAMP:" + utc(now),
                  "DTSTART:" + utc(event["start"]), "DTEND:" + utc(event["end"]),
                  "SUMMARY:" + escape(event["summary"])]
        if event.get("location"):
            lines.append("LOCATION:" + escape(event["location"]))
        if event.get("description"):
            lines.append("DESCRIPTION:" + escape(event["description"]))
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return ("\r\n".join(fold(line) for line in lines) + "\r\n").encode()
