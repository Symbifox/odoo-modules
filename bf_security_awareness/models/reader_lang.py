"""Language of whoever reads a text this module writes for someone else.

A chatter note, an activity, a stored name or an ntfy alert is often written in
a request that is not its reader's: the public phishing page (public user), the
mail gateway or a scheduled job (OdooBot), an employee's report read by the
security team. ``_()`` would then take the writer's language. These helpers pin
the reader's language on the records, so that ``records.env._()`` and
``_description_selection(records.env)`` follow it.
"""

from odoo import SUPERUSER_ID


def reader_lang(env, *candidates):
    """The first candidate language installed on the database, else None."""
    installed = {code for code, _label in env["res.lang"].get_installed()}
    for lang in candidates:
        if lang and lang in installed:
            return lang
    return None


def reader_env(records, *candidates):
    """``records`` with the first installed candidate language in its context.

    Without any installed candidate the context is left as it is.
    """
    lang = reader_lang(records.env, *candidates)
    return records.with_context(lang=lang) if lang else records


def company_env(records, company=None):
    """``records`` in the language of the company's operators (security team)."""
    company = company or records.env.company
    return reader_env(records, company.sudo().partner_id.lang)


def operator_env(records, company=None):
    """``records`` in the language of the operator who acts, else the company's.

    The acting user counts only when an internal person acts. The public user
    (phishing page) and OdooBot (scheduled jobs, mail gateway) are no reader:
    the company's language is taken instead.
    """
    company = company or records.env.company
    user = records.env.user
    acting = user.lang if (
        user.id != SUPERUSER_ID and user.active and not user.share) else None
    return reader_env(records, acting, company.sudo().partner_id.lang)
