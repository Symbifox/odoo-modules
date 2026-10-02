"""Retention of the shift records, without the ORM.

Hours, premiums, indemnities and benefits are supporting documents of the
pay: the tax laws keep them 6 years after the end of the last year they
relate to (Tax Administration Act, s. 35.1; Income Tax Act, 230(4)), which
covers the 3 years of the labour standards record (N-1.1, r. 6, s. 2). Once
that is over, the private sector privacy act asks to destroy them
(P-39.1, s. 23), unless a dispute is under way.

Two questions are answered here: from which day a record is still kept,
and which old records must stay because a record that stays points to them.
"""

from datetime import date, timedelta

MIN_YEARS = 6
# Hours worked in late December are often paid in January, and the pay
# belongs to the tax year it is paid in: a month's margin keeps them a year
# longer.
PAY_LAG_DAYS = 31


def cutoff(today, years=MIN_YEARS):
    """First last-day that is still kept.

    A record whose last day falls before it was paid, at the latest, in the
    year ``today.year - years - 1``: its ``years`` years after the end of
    that year are over.
    """
    return date(today.year - years, 1, 1) - timedelta(days=PAY_LAG_DAYS)


def destroyable(candidates, links):
    """The candidates that can go.

    ``links`` are pairs ``(a, b)``: if ``a`` stays, ``b`` stays too (``a``
    points to ``b``; a swap between two schedules gives both pairs). Anything
    that is not a candidate stays, so a chain of links is followed to the end.
    """
    gone = set(candidates)
    changed = True
    while changed:
        changed = False
        for a, b in links:
            if b in gone and a not in gone:
                gone.discard(b)
                changed = True
    return gone
