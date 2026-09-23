"""Jours ouvrables et jours fériés du Québec, sans rien d'Odoo.

Les huit jours fériés, chômés et payés de la Loi sur les normes du travail,
tels que les énumère la CNESST (vérifié le 2026-09-22) :

* le 1er janvier ;
* le Vendredi saint **ou** le lundi de Pâques, au choix de l'employeur ;
* le lundi qui précède le 25 mai (Journée nationale des patriotes) ;
* le 24 juin, ou le 25 si le 24 tombe un dimanche ;
* le 1er juillet, ou le 2 si le 1er tombe un dimanche ;
* le premier lundi de septembre ;
* le deuxième lundi d'octobre ;
* le 25 décembre.

Ce n'est PAS la liste des délais judiciaires (Code de procédure civile), qui
compte d'autres jours : le panneau le dit.
"""

from datetime import date, timedelta

from dateutil.easter import easter

MAX_DAYS = 3660  # dix ans : borne des boucles de jours ouvrables


def _nth_weekday(year, month, weekday, n):
    d = date(year, month, 1)
    d += timedelta(days=(weekday - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


def quebec_holidays(year, easter_day="friday"):
    """{date: nom} pour une année. ``easter_day`` : ``friday`` ou ``monday``."""
    paques = easter(year)
    holidays = {
        date(year, 1, 1): "Jour de l'An",
        (paques - timedelta(days=2) if easter_day != "monday"
         else paques + timedelta(days=1)):
            "Vendredi saint" if easter_day != "monday" else "Lundi de Pâques",
        _patriotes(year): "Journée nationale des patriotes",
        _sunday_to_monday(date(year, 6, 24)): "Fête nationale du Québec",
        _sunday_to_monday(date(year, 7, 1)): "Fête du Canada",
        _nth_weekday(year, 9, 0, 1): "Fête du Travail",
        _nth_weekday(year, 10, 0, 2): "Action de grâce",
        date(year, 12, 25): "Noël",
    }
    return holidays


def _patriotes(year):
    d = date(year, 5, 24)
    while d.weekday() != 0:
        d -= timedelta(days=1)
    return d


def _sunday_to_monday(d):
    return d + timedelta(days=1) if d.weekday() == 6 else d


def _holidays_between(start, end, easter_day):
    found = {}
    for year in range(min(start, end).year, max(start, end).year + 1):
        found.update(quebec_holidays(year, easter_day))
    return found


def is_business_day(d, holidays):
    return d.weekday() < 5 and d not in holidays


def business_days_between(start, end, easter_day="friday"):
    """Jours ouvrables de ``start`` (exclu) à ``end`` (inclus) ; négatif à rebours.
    Rend aussi les fériés rencontrés, pour que le panneau les nomme."""
    if abs((end - start).days) > MAX_DAYS:
        raise ValueError("too_far")
    holidays = _holidays_between(start, end, easter_day)
    step = 1 if end >= start else -1
    count, skipped, d = 0, [], start
    while d != end:
        d += timedelta(days=step)
        if is_business_day(d, holidays):
            count += step
        elif d in holidays and d.weekday() < 5:
            skipped.append((d, holidays[d]))
    return count, skipped


def add_business_days(start, n, easter_day="friday"):
    """``start`` + ``n`` jours ouvrables (``n`` négatif : à rebours)."""
    if abs(n) > MAX_DAYS:
        raise ValueError("too_far")
    step = 1 if n >= 0 else -1
    holidays = _holidays_between(start, start + timedelta(days=int(n * 1.6) + 30 * step),
                                 easter_day)
    d, left, skipped = start, abs(n), []
    while left:
        d += timedelta(days=step)
        if d.year not in {h.year for h in holidays}:
            holidays.update(quebec_holidays(d.year, easter_day))
        if is_business_day(d, holidays):
            left -= 1
        elif d in holidays and d.weekday() < 5:
            skipped.append((d, holidays[d]))
    return d, skipped
