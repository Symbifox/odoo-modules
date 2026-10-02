"""Time-zone helpers shared by the models.

Odoo stores datetimes in UTC; every labour rule speaks in wall-clock hours.
Conversions go through the employee's time zone, then the company's, then
Montréal's (America/Toronto shares its rules).
"""

import logging
from datetime import datetime, time, timedelta

import pytz

_logger = logging.getLogger(__name__)

DEFAULT_TZ = "America/Toronto"


def tz_of(employee=None, env=None):
    name = None
    if employee:
        emp = employee.sudo()
        name = emp.tz or emp.company_id.partner_id.tz
    if not name and env is not None:
        name = env.user.tz or env.company.partner_id.tz
    try:
        return pytz.timezone(name or DEFAULT_TZ)
    except pytz.UnknownTimeZoneError:
        return pytz.timezone(DEFAULT_TZ)


def to_local(dt, tz):
    """UTC naive -> local naive."""
    if not dt:
        return dt
    return pytz.utc.localize(dt).astimezone(tz).replace(tzinfo=None)


def to_utc(dt, tz):
    """Local naive -> UTC naive. Ambiguous or missing wall times (the two
    daylight-saving changes) resolve to standard time."""
    if not dt:
        return dt
    return tz.localize(dt, is_dst=False).astimezone(pytz.utc).replace(tzinfo=None)


def float_to_time(hour):
    whole = int(hour)
    minutes = int(round((hour - whole) * 60))
    if minutes == 60:
        whole, minutes = whole + 1, 0
    return whole, minutes


def local_bounds(day, hour_from, hour_to):
    """Local start and end of a shift that starts on ``day``. An end at or
    before the start is on the next day (a night shift)."""
    h0, m0 = float_to_time(hour_from)
    h1, m1 = float_to_time(hour_to)
    start = datetime.combine(day, time(0, 0)) + timedelta(hours=h0, minutes=m0)
    end = datetime.combine(day, time(0, 0)) + timedelta(hours=h1, minutes=m1)
    if end <= start:
        end += timedelta(days=1)
    return start, end


def fmt_num(env, value):
    """A number in the user's language, without useless decimals:
    30, 9,5, 2,25 in French; 30, 9.5, 2.25 in English."""
    from odoo.tools.misc import formatLang

    if value is None:
        return ""
    for digits in (0, 1, 2):
        if abs(value * 10 ** digits - round(value * 10 ** digits)) < 1e-9:
            break
    return formatLang(env, value, digits=digits)


def is_shift_manager(env):
    return env.su or env.user.has_group("bf_shift.group_shift_manager")


def guard_employee_vals(env, vals, allowed):
    """An employee writes only the fields of the form they fill in.

    Access rights let an employee create and edit their own availability,
    benefit events and swap requests; without this, a direct RPC call could
    also set the state, a computed verdict or the approver.
    """
    from odoo.exceptions import AccessError

    if is_shift_manager(env):
        return
    # The web client sends the status bar's initial value with a new record.
    extra = {k for k in set(vals) - set(allowed) if not (k == "state" and vals[k] == "draft")}
    if extra:
        # env._, not _(): outside a method, _() finds no language over RPC.
        raise AccessError(env._("You cannot set these fields yourself: %(fields)s",
                                fields=", ".join(sorted(extra))))


def employee_defaults(model, values, allowed):
    """For an employee, the defaults of the fields she may not set come from
    the code only.

    ``default_get`` is where Odoo reads the defaults a caller chooses: the
    context (``default_<field>``) and her own ``ir.default`` values, which
    every internal user may set. It runs inside ``create``, after the checks
    on ``vals``: without this, a direct call could create an event already
    approved or a swap already awaiting approval. A manager keeps them all.
    """
    if is_shift_manager(model.env):
        return values
    kept = {}
    for name, value in values.items():
        field = model._fields.get(name)
        if name in allowed or not field:
            kept[name] = value
        elif field.default:
            kept[name] = field.convert_to_write(
                field.convert_to_cache(field.default(model), model), model)
    return kept


def check_own(records, field="employee_id"):
    """Record rules are checked before a write, not after: an employee could
    otherwise move their own record onto a colleague. Checked after."""
    from odoo.exceptions import AccessError

    if is_shift_manager(records.env):
        return
    user = records.env.user
    for rec in records:
        if rec[field].sudo().user_id != user:
            raise AccessError(records.env._("This record must stay yours."))


def lang_of(env, partner=None, user=None):
    """The language a person reads: theirs, else the company's."""
    lang = (user and user.sudo().lang) or (partner and partner.sudo().lang)
    return lang or env.company.partner_id.lang or env.lang or "en_US"


def _author(record):
    """Who signs a chatter message. Odoo refuses a message whose author has no
    email address, even an internal note, and the refusal would undo the
    action that posts it (a publication, a swap). The company signs instead;
    None when it has no address either."""
    if record.env.user.partner_id.email:
        return {}
    company = record.env.company.partner_id
    if company.email:
        return {"author_id": company.id}
    return None


def post(record, **kwargs):
    """``message_post`` that never undoes the action it reports."""
    author = _author(record)
    if author is None:
        _logger.warning("bf_shift: message not posted on %s, no sender address", record)
        return record.env["mail.message"]
    return record.message_post(**kwargs, **author)


def assign_activity(record, user, assigner, build, deadline=None):
    """Give ``user`` a to-do on ``record``, attributed to ``assigner``, and
    tell them in their own language only.

    ``assigner`` is the person responsible for the schedule. It is never
    whoever triggered the action: after a refusal, that is the colleague who
    refused, and Odoo's own notice ("X has just assigned you an activity")
    would name her and reveal the refusal. Without a responsible person, the
    activity is attributed to the system and the notice is signed by the
    company.

    Odoo's own notice is switched off (it writes its subject and its
    "Activity / Deadline" header in the language of whoever acts); the one
    sent here is built entirely in ``env``'s language by ``build(env)``, which
    returns ``(summary, subject, body)``.
    """
    from odoo import SUPERUSER_ID, fields as ofields
    from odoo.tools.misc import get_lang

    env = record.env
    lang = lang_of(env, user=user)
    by = assigner if assigner and assigner.active else env["res.users"].browse(SUPERUSER_ID)
    rec = record.with_user(by).sudo().with_context(lang=lang, mail_activity_quick_update=True)
    summary, subject, body = build(rec.env)
    todo = rec.env.ref("mail.mail_activity_data_todo")
    activity = rec.activity_schedule(
        "mail.mail_activity_data_todo", user_id=user.id, summary=summary,
        date_deadline=deadline or ofields.Date.context_today(rec))
    # Who signs the notice: the responsible person, unless they would be
    # writing to themselves or have no address; then the company.
    company = (record.company_id if "company_id" in record._fields and record.company_id
               else env.company).partner_id
    # Signed by whoever the activity is attributed to: an archived
    # responsible gives the activity to the system, and the company signs.
    author = by.partner_id if (by.id != SUPERUSER_ID and by != user
                               and by.partner_id.email) else company
    if not author.email:
        _logger.warning("bf_shift: activity notice not sent on %s, no sender address", record)
        return activity
    # Odoo drops the acting user from the recipients (the "real author"),
    # whoever signs: without mail_notify_author, a responsible told of their
    # own list running out would get the message, never the email.
    rec.with_context(mail_notify_author=True).message_notify(
        partner_ids=user.partner_id.ids, body=body, subject=subject, author_id=author.id,
        email_layout_xmlid="mail.mail_notification_layout", record_name=subject,
        model_description=rec.env._("Open shift offer"),
        subtitles=[rec.env._("Activity: %s", todo.with_context(lang=lang).name),
                   rec.env._("Deadline: %s", activity.date_deadline.strftime(
                       get_lang(rec.env).date_format))])
    return activity


def notify_each_in_their_language(record, partners, build):
    """Post one notification per language, each written in that language.

    ``build(env)`` returns ``(body, subject, model_description)`` with its
    strings translated through ``env._``: a plain ``_()`` would translate in
    the language of whoever triggered the action, not of whoever reads.
    """
    groups = {}
    for partner in partners.sudo():
        groups.setdefault(lang_of(record.env, partner=partner), record.env["res.partner"])
        groups[lang_of(record.env, partner=partner)] |= partner
    author = _author(record)
    if author is None:
        _logger.warning("bf_shift: notice not sent on %s, no sender address", record)
        return
    for lang, group in groups.items():
        rec = record.with_context(lang=lang)
        body, subject, description = build(rec.env)
        rec.message_notify(partner_ids=group.ids, body=body, subject=subject,
                           model_description=description, **author)


# Internal flags (skip the log, stay silent, consent already given, why)
# travel in the context, and a caller controls its own context over RPC.
# They are therefore honoured only next to this marker, which no JSON or
# XML-RPC payload can carry: an object compared by identity.
_INTERNAL = object()
_PREFIX = "bf_shift_"


def internal(records, **flags):
    """``records`` with trusted flags, and every flag the caller passed dropped."""
    ctx = {k: v for k, v in records.env.context.items() if not k.startswith(_PREFIX)}
    ctx[_PREFIX + "internal"] = _INTERNAL
    for name, value in flags.items():
        ctx[_PREFIX + name] = value
    return records.with_context(ctx)


def flag(env, name):
    """A trusted internal flag, or None."""
    if env.context.get(_PREFIX + "internal") is not _INTERNAL:
        return None
    return env.context.get(_PREFIX + name)
