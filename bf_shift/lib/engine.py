"""Shift engine: pure Python, no Odoo import.

Everything here works on *local* naive datetimes (the employee's wall clock),
because every rule of the Act respecting labour standards (LNT) and of the
collective agreements is expressed in local hours: a night premium starts at
midnight on the wall, not at midnight UTC.

Three jobs:

- ``most_favourable``: merge an agreement's parameters with the LNT floor.
  The rule most favourable to the employee always wins, so an agreement can
  raise the floor but never lower it.
- ``check_segments``: the warnings shown before a schedule is published.
  They never block: the LNT gives the employee a right to refuse, it does
  not forbid the employer to ask.
- ``compute_pay``: turn worked segments into coded hours (regular,
  overtime, double time, premiums, call-back top-up, bank) for a pay period.
"""

from collections import defaultdict
from dataclasses import dataclass, field, fields, replace
from datetime import date, datetime, time, timedelta, timezone

WORKED_KINDS = ("work", "callback")


# ---------------------------------------------------------------------------
# Parameters and the LNT floor
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Params:
    """Rules of one agreement. Defaults are the LNT floor."""

    week_start: int = 6                 # Python weekday: Monday = 0, Sunday = 6
    ot_weekly_threshold: float = 40.0   # art. 52
    ot_daily_threshold: float = 0.0     # 0 = none (the LNT has none)
    ot_multiplier: float = 1.5          # art. 55
    dt_weekly_threshold: float = 0.0    # 0 = none
    dt_multiplier: float = 2.0
    dt_seventh_day: bool = False
    dt_weekdays: frozenset = frozenset()  # overtime on these weekdays is double
    bank_multiplier: float = 1.5        # art. 55: bank at time and a half
    callback_min_hours: float = 3.0     # art. 58
    notice_days: float = 5.0            # art. 59.0.1, 3rd paragraph
    max_extra_daily: float = 2.0        # art. 59.0.1, 1st paragraph
    max_24h: float = 14.0               # art. 59.0.1
    max_24h_flexible: float = 12.0      # art. 59.0.1, flexible or no fixed hours
    max_weekly: float = 50.0            # art. 59.0.1, 2nd paragraph
    weekly_rest_hours: float = 32.0     # art. 78
    meal_after_hours: float = 5.0       # art. 79
    meal_minutes: float = 30.0          # art. 79
    min_rest_between: float = 0.0       # 0 = none (agreements only)
    holiday_indemnity: bool = True      # art. 62: 1/20 of the 4 previous weeks


LNT = Params()

# For each numeric parameter, which direction favours the employee.
# "min": a lower value is more favourable; "max": a higher one is.
_FAVOURS = {
    "ot_weekly_threshold": "min",
    "ot_multiplier": "max",
    "bank_multiplier": "max",
    "callback_min_hours": "max",
    "notice_days": "max",
    "max_extra_daily": "min",
    "max_24h": "min",
    "max_24h_flexible": "min",
    "max_weekly": "min",
    "weekly_rest_hours": "max",
    "meal_after_hours": "min",
    "meal_minutes": "max",
}


def most_favourable(params):
    """Return ``(effective, below_floor)``.

    ``effective`` is ``params`` with every LNT-governed value replaced by the
    floor wherever the agreement is less favourable. ``below_floor`` lists the
    names of the parameters that were raised, so the agreement form can say
    so instead of silently ignoring what was typed.
    """
    changes = {}
    below = []
    for name, direction in _FAVOURS.items():
        mine = getattr(params, name)
        floor = getattr(LNT, name)
        if direction == "min" and mine > floor:
            changes[name] = floor
            below.append(name)
        elif direction == "max" and mine < floor:
            changes[name] = floor
            below.append(name)
    if not params.holiday_indemnity:
        changes["holiday_indemnity"] = True
        below.append("holiday_indemnity")
    return replace(params, **changes), below


# ---------------------------------------------------------------------------
# Segments
# ---------------------------------------------------------------------------

@dataclass
class Segment:
    """One stretch of time for one employee, in local naive datetimes.

    ``kind``: ``work`` (a shift), ``callback`` (recalled outside the schedule),
    ``on_call`` (on call at home: not worked time, art. 57), ``leave`` (paid
    leave or a holiday off: counts toward the week, LNT art. 52), and
    ``bank_leave`` (time taken from the bank).
    """

    key: object
    start: datetime
    end: datetime
    break_hours: float = 0.0
    break_paid: bool = False
    kind: str = "work"
    to_bank: bool = False
    informed_at: datetime = None
    tz: object = None   # pytz time zone: durations in real time across DST changes

    @property
    def span_hours(self):
        return max(0.0, hours_between(self.start, self.end, self.tz))

    @property
    def paid_hours(self):
        unpaid = 0.0 if self.break_paid else self.break_hours
        return max(0.0, self.span_hours - unpaid)

    @property
    def day(self):
        return self.start.date()


def week_start_of(day, week_start):
    """First day of the pay week that contains ``day``."""
    return day - timedelta(days=(day.weekday() - week_start) % 7)


def _absolute(dt, tz):
    """Local wall time -> UTC. The ambiguous hour of the autumn change
    resolves to standard time; the missing hour of spring moves forward."""
    if tz is None:
        return dt
    return tz.normalize(tz.localize(dt, is_dst=False)).astimezone(timezone.utc).replace(tzinfo=None)


def hours_between(a, b, tz=None):
    """Hours actually elapsed between two local wall times: the night of the
    autumn change lasts one hour more than the clock says, spring one less."""
    return (_absolute(b, tz) - _absolute(a, tz)).total_seconds() / 3600.0


def plus_hours(dt, hours, tz=None):
    """Local wall time ``hours`` real hours after ``dt``."""
    if tz is None:
        return dt + timedelta(hours=hours)
    real = _absolute(dt, tz) + timedelta(hours=hours)
    return tz.fromutc(real).replace(tzinfo=None)


def _overlap_hours(a0, a1, b0, b1, tz=None):
    lo = max(a0, b0)
    hi = min(a1, b1)
    if hi <= lo:
        return 0.0
    return max(0.0, hours_between(lo, hi, tz))


def _days_between(start, end):
    d = start.date()
    last = end.date()
    while d <= last:
        yield d
        d += timedelta(days=1)


# ---------------------------------------------------------------------------
# Premiums
# ---------------------------------------------------------------------------

@dataclass
class PremiumRule:
    code: str
    label: str = ""
    applies_on: str = "window"          # window | weekdays | holidays
    hour_from: float = 0.0              # window start, local hour (may wrap)
    hour_to: float = 0.0
    weekdays: frozenset = frozenset()   # empty = every day
    method: str = "percent"             # percent | amount
    percent: float = 0.0                # 7.0 means 7 %
    amount: float = 0.0                 # per hour, for method "amount"
    floor: float = 0.0                  # minimum per hour, for method "percent"
    enhanced_percent: float = 0.0       # 0 = no enhanced rate
    enhanced_threshold: float = 0.0     # hours worked in the block
    enhanced_period_days: int = 14
    majority: bool = False              # whole shift if most of it is in the window
    date_from: date = None
    date_to: date = None
    key: object = None

    def active_on(self, day):
        if self.date_from and day < self.date_from:
            return False
        if self.date_to and day > self.date_to:
            return False
        return True


def _clock(day, hour):
    whole = int(hour)
    minutes = int(round((hour - whole) * 60))
    if whole >= 24:
        return datetime.combine(day + timedelta(days=1), time(0, 0))
    return datetime.combine(day, time(whole, minutes))


def _rule_intervals(rule, day, holidays):
    """Intervals of calendar day ``day`` covered by ``rule``."""
    if rule.weekdays and day.weekday() not in rule.weekdays:
        return []
    start_of_day = datetime.combine(day, time(0, 0))
    end_of_day = start_of_day + timedelta(days=1)
    if rule.applies_on == "holidays":
        return [(start_of_day, end_of_day)] if day in holidays else []
    if rule.applies_on == "weekdays":
        return [(start_of_day, end_of_day)]
    if rule.hour_from == rule.hour_to:
        return [(start_of_day, end_of_day)]
    if rule.hour_from < rule.hour_to:
        return [(_clock(day, rule.hour_from), _clock(day, rule.hour_to))]
    # Wrapping window, 23:00 -> 07:00: on each calendar day it covers the
    # morning part and the evening part.
    return [(start_of_day, _clock(day, rule.hour_to)),
            (_clock(day, rule.hour_from), end_of_day)]


def premium_hours(segment, rule, holidays=frozenset()):
    """Paid hours of ``segment`` that earn ``rule``.

    The unpaid break's position inside the shift is not recorded, so it is
    spread over the shift in proportion. With ``majority``, the whole shift
    earns the premium when more than half of it falls in the window, and none
    of it otherwise.
    """
    span = segment.span_hours
    if span <= 0 or segment.kind not in WORKED_KINDS:
        return 0.0
    if not rule.active_on(segment.day):
        return 0.0
    covered = 0.0
    for day in _days_between(segment.start, segment.end):
        for w0, w1 in _rule_intervals(rule, day, holidays):
            covered += _overlap_hours(segment.start, segment.end, w0, w1, segment.tz)
    covered = min(covered, span)
    if rule.majority:
        return segment.paid_hours if covered * 2 > span else 0.0
    return covered * segment.paid_hours / span


def premium_per_hour(rule, hourly_rate, enhanced=False):
    if rule.method == "amount":
        return rule.amount
    pct = rule.enhanced_percent if (enhanced and rule.enhanced_percent) else rule.percent
    return max((hourly_rate or 0.0) * pct / 100.0, rule.floor or 0.0)


# ---------------------------------------------------------------------------
# Pay computation
# ---------------------------------------------------------------------------

@dataclass
class Line:
    code: str
    hours: float = 0.0
    multiplier: float = 1.0
    amount: float = 0.0
    keys: set = field(default_factory=set)
    label: str = ""
    enhanced: bool = False


class _Lines:
    def __init__(self):
        self._lines = {}

    def add(self, code, hours, multiplier=1.0, amount=0.0, key=None, label="",
            enhanced=False):
        if hours <= 1e-9 and abs(amount) <= 1e-9:
            return
        slot = (code, round(multiplier, 4), enhanced)
        line = self._lines.get(slot)
        if line is None:
            line = self._lines[slot] = Line(code, 0.0, multiplier, 0.0, set(), label,
                                            enhanced)
        line.hours += hours
        line.amount += amount
        if key is not None:
            line.keys.add(key)

    def result(self):
        out = []
        for line in self._lines.values():
            line.hours = round(line.hours, 4)
            line.amount = round(line.amount, 2)
            out.append(line)
        order = {"REG": 0, "OT": 1, "DT": 2, "BANKIN": 3, "BANKOUT": 4,
                 "LEAVE": 5, "CBTOP": 6, "ONCALL": 7, "HOLIND": 8}
        out.sort(key=lambda l: (order.get(l.code, 50), l.code, l.multiplier))
        return out


def _split_hours(segment, params, state):
    """Split one worked segment into regular / overtime / double time.

    ``state`` carries the running counters of the week. Daily overtime is
    taken first and does not count toward the weekly threshold, so an hour
    is never paid overtime twice (no pyramiding).
    """
    hours = segment.paid_hours
    day = segment.day
    seventh = (params.dt_seventh_day
               and len(state["days"] - {day}) >= 6)
    if seventh:
        state["days"].add(day)
        state["daily"][day] += hours
        state["total"] += hours
        return 0.0, 0.0, hours

    room = max(0.0, params.ot_weekly_threshold - state["weekly_regular"])
    if params.ot_daily_threshold > 0:
        room = min(room, max(0.0, params.ot_daily_threshold - state["daily"][day]))
    regular = min(hours, room)
    overtime = hours - regular

    double = 0.0
    if overtime > 0:
        if day.weekday() in params.dt_weekdays:
            double = overtime
        elif params.dt_weekly_threshold > 0:
            before = state["total"]
            after = before + hours
            double = min(overtime,
                         max(0.0, after - max(params.dt_weekly_threshold, before)))
    overtime -= double

    state["days"].add(day)
    state["daily"][day] += hours
    state["weekly_regular"] += regular
    state["total"] += hours
    return regular, overtime, double


def compute_pay(segments, params, rules=(), holidays=frozenset(), hourly_rate=0.0,
                emit_from=None, emit_to=None):
    """Coded pay lines for one employee.

    ``segments`` must cover every week that touches the emitted range, so the
    weekly overtime threshold is counted on the whole week; only the segments
    that start inside ``[emit_from, emit_to]`` produce lines.
    """
    params, _below = most_favourable(params)
    rate = hourly_rate or 0.0
    lines = _Lines()
    segs = sorted(segments, key=lambda s: (s.start, s.end))

    def emitted(seg):
        if emit_from and seg.day < emit_from:
            return False
        if emit_to and seg.day > emit_to:
            return False
        return True

    weeks = defaultdict(list)
    for seg in segs:
        weeks[week_start_of(seg.day, params.week_start)].append(seg)

    for _ws, week in sorted(weeks.items()):
        state = {"weekly_regular": 0.0, "total": 0.0,
                 "daily": defaultdict(float), "days": set()}
        for seg in week:
            out = emitted(seg)
            if seg.kind == "leave":
                # LNT art. 52: paid leave and holidays count toward the week.
                state["weekly_regular"] += seg.paid_hours
                if out:
                    lines.add("LEAVE", seg.paid_hours, 1.0, seg.paid_hours * rate, seg.key)
                continue
            if seg.kind == "bank_leave":
                if out:
                    lines.add("BANKOUT", seg.paid_hours, 1.0, seg.paid_hours * rate, seg.key)
                continue
            if seg.kind == "on_call":
                if out:
                    lines.add("ONCALL", seg.paid_hours, 0.0, 0.0, seg.key)
                continue
            if seg.kind not in WORKED_KINDS:
                continue
            regular, overtime, double = _split_hours(seg, params, state)
            if not out:
                continue
            lines.add("REG", regular, 1.0, regular * rate, seg.key)
            if seg.to_bank:
                lines.add("BANKIN", overtime * params.bank_multiplier,
                          params.bank_multiplier, 0.0, seg.key)
                lines.add("BANKIN", double * params.dt_multiplier,
                          params.dt_multiplier, 0.0, seg.key)
            else:
                lines.add("OT", overtime, params.ot_multiplier,
                          overtime * rate * params.ot_multiplier, seg.key)
                lines.add("DT", double, params.dt_multiplier,
                          double * rate * params.dt_multiplier, seg.key)
            if seg.kind == "callback":
                top_up = max(0.0, params.callback_min_hours - seg.paid_hours)
                lines.add("CBTOP", top_up, 1.0, top_up * rate, seg.key)

    # Premiums: computed on every worked hour, apart from overtime (LNT art.
    # 55: overtime is paid on the usual wage, premiums excluded).
    worked = [s for s in segs if s.kind in WORKED_KINDS and emitted(s)]
    for rule in rules:
        blocks = _enhanced_blocks(worked, rule, emit_from) if rule.enhanced_percent else {}
        for seg in worked:
            hours = premium_hours(seg, rule, holidays)
            if hours <= 0:
                continue
            enhanced = bool(blocks.get(_block_of(seg.day, rule, emit_from)))
            per_hour = premium_per_hour(rule, rate, enhanced)
            lines.add(rule.code, hours, 1.0, hours * per_hour, seg.key, rule.label,
                      enhanced)

    if params.holiday_indemnity:
        for holiday in sorted(holidays):
            if emit_from and holiday < emit_from:
                continue
            if emit_to and holiday > emit_to:
                continue
            hours = holiday_indemnity_hours(segs, params, holiday)
            lines.add("HOLIND", hours, 1.0, hours * rate, holiday)
    return lines.result()


def _block_of(day, rule, origin):
    origin = origin or date(2000, 1, 2)
    return (day - origin).days // max(1, rule.enhanced_period_days)


def _enhanced_blocks(worked, rule, origin):
    totals = defaultdict(float)
    for seg in worked:
        totals[_block_of(seg.day, rule, origin)] += seg.paid_hours
    return {block: total >= rule.enhanced_threshold for block, total in totals.items()}


def holiday_indemnity_hours(segments, params, holiday):
    """LNT art. 62: 1/20 of the wages of the 4 complete weeks before the week
    of the holiday, overtime excluded. Returned in hours at the usual rate:
    each week is capped at the overtime threshold."""
    this_week = week_start_of(holiday, params.week_start)
    first = this_week - timedelta(days=28)
    per_week = defaultdict(float)
    for seg in segments:
        if seg.kind not in WORKED_KINDS + ("leave",):
            continue
        if first <= seg.day < this_week:
            per_week[week_start_of(seg.day, params.week_start)] += seg.paid_hours
    total = sum(min(h, params.ot_weekly_threshold) for h in per_week.values())
    return total / 20.0


# ---------------------------------------------------------------------------
# Checks before publishing
# ---------------------------------------------------------------------------

@dataclass
class Warning:
    code: str
    key: object
    values: dict = field(default_factory=dict)


def _merged(intervals):
    out = []
    for a, b in sorted(intervals):
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def check_segments(segments, params, usual_day_hours=8.0, flexible=False,
                   availability_required=False, unavailable=(), only_keys=None):
    """Warnings for one employee.

    ``segments`` should include every segment of the weeks concerned, even
    from other schedules, so the weekly limits see the whole week;
    ``only_keys`` restricts which segments may carry a warning.
    ``unavailable`` is a list of ``(start, end)`` local intervals.
    """
    params, _below = most_favourable(params)
    warns = []
    worked = sorted((s for s in segments if s.kind in WORKED_KINDS),
                    key=lambda s: s.start)

    def mine(seg):
        return only_keys is None or seg.key in only_keys

    # Double booking, and rest between shifts.
    for prev, cur in zip(worked, worked[1:]):
        if cur.start < prev.end and (mine(cur) or mine(prev)):
            warns.append(Warning("overlap", cur.key if mine(cur) else prev.key,
                                 {"other": prev.key if mine(cur) else cur.key}))
        elif params.min_rest_between > 0 and mine(cur):
            gap = hours_between(prev.end, cur.start, cur.tz)
            if gap < params.min_rest_between:
                warns.append(Warning("rest_between", cur.key,
                                     {"hours": round(gap, 2),
                                      "limit": params.min_rest_between}))

    for seg in worked:
        if not mine(seg):
            continue
        # Meal break, art. 79.
        if seg.span_hours > params.meal_after_hours and \
                seg.break_hours * 60 < params.meal_minutes - 1e-6:
            warns.append(Warning("meal", seg.key,
                                 {"minutes": params.meal_minutes,
                                  "after": params.meal_after_hours}))
        # Notice, art. 59.0.1: informed fewer than N days before.
        if seg.informed_at and not availability_required:
            lead = hours_between(seg.informed_at, seg.start, seg.tz) / 24.0
            if lead < params.notice_days:
                warns.append(Warning("notice", seg.key,
                                     {"days": round(max(lead, 0.0), 1),
                                      "limit": params.notice_days}))
        # 24 hours from the start of this shift.
        limit24 = params.max_24h_flexible if flexible else params.max_24h
        horizon = plus_hours(seg.start, 24, seg.tz)
        in24 = 0.0
        for other in worked:
            span = other.span_hours
            if span <= 0:
                continue
            inside = _overlap_hours(other.start, other.end, seg.start, horizon, seg.tz)
            in24 += inside * other.paid_hours / span
        if in24 > limit24 + 1e-6:
            warns.append(Warning("max_24h", seg.key,
                                 {"hours": round(in24, 2), "limit": limit24}))
        # Declared unavailability.
        for u0, u1 in unavailable:
            if _overlap_hours(seg.start, seg.end, u0, u1, seg.tz) > 0:
                warns.append(Warning("unavailable", seg.key, {}))
                break

    # Per day: more than 2 hours beyond the usual day, art. 59.0.1.
    per_day = defaultdict(list)
    for seg in worked:
        per_day[seg.day].append(seg)
    for day, segs in per_day.items():
        total = sum(s.paid_hours for s in segs)
        if total > usual_day_hours + params.max_extra_daily + 1e-6:
            target = next((s for s in reversed(segs) if mine(s)), None)
            if target is not None:
                warns.append(Warning("daily_extra", target.key,
                                     {"hours": round(total, 2),
                                      "limit": usual_day_hours + params.max_extra_daily}))

    # Per week: 50 hours, and 32 consecutive hours of rest.
    per_week = defaultdict(list)
    for seg in worked:
        per_week[week_start_of(seg.day, params.week_start)].append(seg)
    for ws, segs in per_week.items():
        target = next((s for s in reversed(segs) if mine(s)), None)
        if target is None:
            continue
        total = sum(s.paid_hours for s in segs)
        if total > params.max_weekly + 1e-6:
            warns.append(Warning("weekly_max", target.key,
                                 {"hours": round(total, 2), "limit": params.max_weekly}))
        w0 = datetime.combine(ws, time(0, 0))
        w1 = w0 + timedelta(days=7)
        busy = _merged((max(s.start, w0), min(s.end, w1)) for s in segs)
        tz = segs[0].tz
        cursor = w0
        longest = 0.0
        for a, b in busy:
            longest = max(longest, hours_between(cursor, a, tz))
            cursor = max(cursor, b)
        longest = max(longest, hours_between(cursor, w1, tz))
        if longest < params.weekly_rest_hours - 1e-6:
            warns.append(Warning("weekly_rest", target.key,
                                 {"hours": round(longest, 2),
                                  "limit": params.weekly_rest_hours}))
    return warns


# ---------------------------------------------------------------------------
# Offering an open shift
# ---------------------------------------------------------------------------

@dataclass
class Candidate:
    key: object
    seniority: date = None          # earlier = more senior
    hours_offered: float = 0.0      # equity counter, for rotation
    tie: int = 0                    # stable tie-breaker (e.g. the record id)
    blocked: str = ""               # non-empty: reason the person is skipped


def rank_candidates(candidates, method="seniority"):
    """Order in which an open shift is offered.

    ``seniority``: most senior first. ``inverse_seniority``: least senior
    first (how some agreements hand out evenings and nights).
    ``rotation``: fewest hours offered so far, then seniority, which is the
    equity rule of agreements where a refusal counts as hours offered.
    Nobody is dropped: blocked candidates stay in the list with their reason,
    because the trace of who was skipped, and why, is the evidence in a
    grievance.
    """
    far = date(9999, 12, 31)

    def seniority_key(c):
        return (c.seniority or far, c.tie)

    if method == "inverse_seniority":
        order = sorted(candidates, key=lambda c: (-(c.seniority or far).toordinal(), c.tie))
    elif method == "rotation":
        order = sorted(candidates, key=lambda c: (c.hours_offered,) + seniority_key(c))
    else:
        order = sorted(candidates, key=seniority_key)
    return order


def fields_of(params):
    return {f.name: getattr(params, f.name) for f in fields(params)}
