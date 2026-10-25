"""Le jour de la personne et ses jours ouvrables.

Fox Quest comptait ses jours en UTC et en jours de calendrier : la constance
cassait sur toute fin de semaine (vendredi puis lundi = 3 jours d'écart, seuil
2), et « aujourd'hui » était celui du serveur, pas celui de la personne (loin
du fuseau du serveur, la journée ne tombait pas au bon moment). Tout ce qui compte un jour passe désormais par ici.

Un jour ouvrable est un jour travaillé du calendrier de la personne (sa fiche
employé, sinon celui de sa société, sinon lundi à vendredi), qui n'est ni un
férié du calendrier, ni un congé posé sur sa ressource (c'est ainsi que
`hr_holidays` pose un congé validé), ni une absence déclarée sur sa fiche
contact (`bf_contact_absence`, si le module est là). Un jour non ouvrable met
la constance en pause : il ne la monte pas et ne la casse pas.
"""
from datetime import datetime, time, timedelta

import pytz

from odoo import fields

MON_TO_FRI = frozenset(range(5))


def user_tz(user):
    try:
        return pytz.timezone(user.tz or 'UTC')
    except pytz.UnknownTimeZoneError:
        return pytz.utc


def local_date(user, value):
    """Le jour, chez la personne, d'un datetime UTC naïf. Une date passe telle quelle."""
    if not value:
        return False
    if isinstance(value, datetime):
        return pytz.utc.localize(value).astimezone(user_tz(user)).date()
    return value


def user_today(user):
    return local_date(user, fields.Datetime.now())


def local_midnight_utc(user, day):
    """Minuit du jour `day` chez la personne, en datetime UTC naïf (borne de domaine)."""
    tz = user_tz(user)
    return tz.localize(datetime.combine(day, time.min)).astimezone(pytz.utc).replace(tzinfo=None)


def _days(first, last):
    day = first
    while day <= last:
        yield day
        day += timedelta(days=1)


def _calendar(user):
    employee = user.employee_id if 'employee_id' in user._fields else False
    return (employee and employee.resource_calendar_id) or user.company_id.resource_calendar_id


def non_working_days(env, user, first, last):
    """Les jours NON ouvrables de la personne, de `first` à `last` inclus."""
    if not first or not last or last < first:
        return set()
    user = user.sudo()
    calendar = _calendar(user)
    attendances = calendar.attendance_ids if calendar else False
    weekdays = {int(d) for d in attendances.mapped('dayofweek')} if attendances else MON_TO_FRI
    off = {day for day in _days(first, last) if day.weekday() not in weekdays}

    lo = local_midnight_utc(user, first)
    hi = local_midnight_utc(user, last + timedelta(days=1))
    Leave = env['resource.calendar.leaves'].sudo()
    domain = [('date_from', '<', hi), ('date_to', '>', lo)]
    if 'time_type' in Leave._fields:
        # « other » = du temps travaillé ailleurs (formation) : la journée compte.
        domain.append(('time_type', '=', 'leave'))
    holidays = [('resource_id', '=', False),
                ('calendar_id', 'in', [calendar.id, False] if calendar else [False]),
                ('company_id', 'in', [user.company_id.id, False])]
    employee = user.employee_id if 'employee_id' in user._fields else False
    if employee and employee.resource_id:
        domain += ['|', '&', '&'] + holidays + [('resource_id', '=', employee.resource_id.id)]
    else:
        domain += holidays
    for leave in Leave.search(domain):
        start = max(local_date(user, leave.date_from), first)
        # Une fin à minuit pile appartient à la veille.
        end = min(local_date(user, leave.date_to - timedelta(seconds=1)), last)
        off.update(_days(start, end))

    if 'bf.partner.absence' in env:
        partners = user.partner_id
        employer = user.partner_id.parent_id or user.company_id.partner_id
        domain = [('date_from', '<=', last), ('date_to', '>=', first)]
        if employer:
            domain += ['|', ('partner_id', '=', partners.id),
                       '&', ('partner_id', '=', employer.id), ('applies_to_children', '=', True)]
        else:
            domain += [('partner_id', '=', partners.id)]
        for absence in env['bf.partner.absence'].sudo().search(domain):
            off.update(_days(max(absence.date_from, first), min(absence.date_to, last)))
    return off


def is_working_day(env, user, day):
    return day not in non_working_days(env, user, day, day)
