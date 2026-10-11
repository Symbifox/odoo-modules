"""Reconstruction of working time from timestamped traces. No ORM here.

A trace is an ``Item``: a moment (a point: a prompt sent, a note written) or a
span (a meeting, a timer segment, a stretch of browser activity). The method,
in this order:

1. a point becomes the few minutes BEFORE it, the time it took to produce it;
2. every span is merged with every other one (union): a minute counts once,
   even when two traces, or two agents, fill it;
3. two sessions closer than the idle gap become one session;
4. sessions are cut at the person's local midnight.

The idle gap is a choice, not a measure: going from 5 to 30 minutes can change
a week by a third or more. It is always reported with its low and high bounds.
"""
from collections import namedtuple
from datetime import datetime, time, timedelta, timezone

Item = namedtuple("Item", "start stop source kind")
# kind: "point", "span" or "meeting" (a span that is a meeting)

DEFAULT_POINT_MINUTES = 2
LOW_GAP, DEFAULT_GAP, HIGH_GAP = 5, 15, 30


def to_spans(items, point_minutes=None):
    """(start, stop, kind) spans in UTC, points widened backwards.

    ``point_minutes`` maps a source to the minutes a point of that source is
    worth; any other source gets ``DEFAULT_POINT_MINUTES``.
    """
    point_minutes = point_minutes or {}
    spans = []
    for item in items:
        start, stop = item.start, item.stop or item.start
        if stop < start:
            start, stop = stop, start
        if item.kind == "point" or stop == start:
            credit = timedelta(minutes=point_minutes.get(item.source, DEFAULT_POINT_MINUTES))
            spans.append((stop - credit, stop, item.kind))
        else:
            spans.append((start, stop, item.kind))
    spans.sort(key=lambda s: (s[0], s[1]))
    return spans


def merge(spans, gap_minutes=0):
    """Union of (start, stop[, ...]) spans; spans closer than the gap join."""
    gap = timedelta(minutes=gap_minutes)
    out = []
    for span in sorted(spans, key=lambda s: (s[0], s[1])):
        start, stop = span[0], span[1]
        if out and start <= out[-1][1] + gap:
            if stop > out[-1][1]:
                out[-1][1] = stop
        else:
            out.append([start, stop])
    return [(a, b) for a, b in out]


def overlap_seconds(spans_a, spans_b):
    """Seconds where two lists of merged spans overlap."""
    i = j = 0
    total = 0.0
    while i < len(spans_a) and j < len(spans_b):
        lo = max(spans_a[i][0], spans_b[j][0])
        hi = min(spans_a[i][1], spans_b[j][1])
        if lo < hi:
            total += (hi - lo).total_seconds()
        if spans_a[i][1] < spans_b[j][1]:
            i += 1
        else:
            j += 1
    return total


def local_day_bounds(day, tz):
    """[start, stop) of a local calendar day, as naive UTC datetimes."""
    start = datetime.combine(day, time.min).replace(tzinfo=tz)
    stop = datetime.combine(day + timedelta(days=1), time.min).replace(tzinfo=tz)
    return (start.astimezone(timezone.utc).replace(tzinfo=None),
            stop.astimezone(timezone.utc).replace(tzinfo=None))


def clip(spans, start, stop):
    """The parts of the spans inside [start, stop)."""
    out = []
    for a, b in spans:
        a, b = max(a, start), min(b, stop)
        if a < b:
            out.append((a, b))
    return out


def hours(spans):
    return sum((b - a).total_seconds() for a, b in spans) / 3600.0


def day_figures(items, day, tz, gap_minutes=DEFAULT_GAP, point_minutes=None, schedule=None, not_before_dt=None):
    """Figures of one local day, from the traces around it (naive UTC datetimes).

    ``items`` should cover the day plus a margin of one gap on each side, so
    that a session crossing midnight is merged before it is cut. ``schedule``
    is the person's working intervals of that day, as naive UTC spans; time
    outside them is "outside hours". Returns a dict.
    """
    spans = to_spans(items, point_minutes)
    if not_before_dt:
        # After widening: a prompt just after the activation does not reach back before it.
        spans = [(max(a, not_before_dt), b, k) for a, b, k in spans if b > not_before_dt]
    day_start, day_stop = local_day_bounds(day, tz)

    def sessions(gap):
        return clip(merge(spans, gap), day_start, day_stop)

    main = sessions(gap_minutes)
    raw_meetings = clip([s[:2] for s in spans if s[2] == "meeting"], day_start, day_stop)
    meetings = merge(raw_meetings)
    figures = {
        "hours": hours(main),
        "hours_low": hours(sessions(min(LOW_GAP, gap_minutes))),
        "hours_high": hours(sessions(max(HIGH_GAP, gap_minutes))),
        "session_count": len(main),
        "first_activity": main[0][0] if main else False,
        "last_activity": main[-1][1] if main else False,
        "span_hours": (main[-1][1] - main[0][0]).total_seconds() / 3600.0 if main else 0.0,
        "longest_session_hours": max(((b - a).total_seconds() / 3600.0 for a, b in main), default=0.0),
        "meeting_hours": hours(meetings),
        "back_to_back_meetings": back_to_back(raw_meetings),
    }
    if schedule is not None:
        inside = overlap_seconds(main, merge(schedule)) / 3600.0
        figures["outside_hours"] = max(0.0, figures["hours"] - inside)
    return figures


def back_to_back(meetings, pause_minutes=10):
    """Meetings that start less than ``pause_minutes`` after the previous one ends.

    ``meetings`` are the meetings one by one, NOT merged: merging would fuse
    two meetings that touch and hide the very join this counts. A meeting that
    starts before the previous one ends overlaps it: they are fused (the same
    meeting from two sources, or a conflict), never counted as a join.
    """
    pause = timedelta(minutes=pause_minutes)
    # The same meeting can come twice (calendar and meeting report): overlapping
    # meetings are fused first, touching ones are not.
    fused = []
    for start, stop in sorted(meetings):
        if fused and start < fused[-1][1]:
            fused[-1][1] = max(fused[-1][1], stop)
        else:
            fused.append([start, stop])
    return sum(1 for prev, nxt in zip(fused, fused[1:]) if nxt[0] - prev[1] < pause)


def source_hours(items, day, tz, point_minutes=None, not_before_dt=None):
    """Hours each source would give alone that day (without gap), for transparency."""
    by_source = {}
    for item in items:
        by_source.setdefault(item.source, []).append(item)
    day_start, day_stop = local_day_bounds(day, tz)
    return {
        source: round(hours(clip(merge(to_spans(its, point_minutes)), max(day_start, not_before_dt or day_start),
                                 day_stop)), 2)
        for source, its in sorted(by_source.items())
    }


def not_before(items, moment):
    """The items, cut so that nothing before ``moment`` remains."""
    out = []
    for item in items:
        stop = item.stop or item.start
        if stop < moment:
            continue
        if item.start < moment:
            item = item._replace(start=moment)
        out.append(item)
    return out
