import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

# Rules removed from security/meeting_security.xml in 18.0.3.45.0. They lived in
# a `noupdate="1"` block, so `ir.model.data._process_end()` will NOT clean them
# up on its own (it skips noupdate rows) — they have to be unlinked here.
OBSOLETE_RULES = (
    'bf_meeting.rule_meeting_attachment_visibility_user',
    'bf_meeting.rule_meeting_attachment_visibility_manager',
)


def migrate(cr, version):
    """Drop the ir.rule pair that used to enforce the attachment visibility window.

    The user-facing rule compared `bf_visible_from` / `bf_visible_until` to
    `time.strftime(...)`, but `ir.rule._compute_domain` is ormcached on
    `(uid, su, model, mode, allowed_company_ids)` — there is no time component
    in the key, so the timestamp froze until the cache was invalidated and the
    window drifted with worker uptime. The check now lives in Python
    (`ir_attachment._search` / `_check_access`), where it is recomputed on every
    call.

    The companion manager rule is dropped too: on its own, a `[(1, '=', 1)]`
    rule on `ir.attachment` for `group_meeting_manager` would OR itself with
    every other group rule and silently widen managers' access beyond what this
    module ever intended to grant.

    Deleted, not neutralised to `[(1, '=', 1)]`. Neutralising looks safer —
    "keep the group's OR branch so we narrow nobody" — but it is not: these two
    are the only `ir.rule` rows on `ir.attachment` (checked on both the Blue Fox
    production database and staging), so there is no other branch to fall back
    to and deleting them simply restores stock Odoo behaviour, where attachment
    access is gated by `ir.attachment.check()` and the linked record's own
    rules. What an always-true rule *would* do is permanent: any rule a future
    module adds on `ir.attachment` for `base.group_user` (a common pattern in
    DMS/document addons) would be OR'd away for every meeting user. That is a
    lasting widening on the one model this feature is supposed to restrict.
    """
    if not version:
        return

    env = api.Environment(cr, SUPERUSER_ID, {})
    for xmlid in OBSOLETE_RULES:
        rule = env.ref(xmlid, raise_if_not_found=False)
        if not rule:
            continue
        _logger.info("bf_meeting: removing obsolete ir.rule %s (id=%s)", xmlid, rule.id)
        rule.unlink()  # also clears the matching ir.model.data row
