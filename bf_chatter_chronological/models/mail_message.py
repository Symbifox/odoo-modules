"""Make mail.message order + chatter pagination follow `date` instead of `id`.

Odoo 18 default: `_order = 'id desc'` and `_message_fetch` paginates via
`('id', '<', before)` / `('id', '>', after)` cursors. This causes imported
emails with a backdated `date` to appear in insertion order rather than in
true chronological order.

This override switches to a compound `(date, id)` cursor, both in the
default ordering and in the chatter's load-more pagination.

Also exposes `action_backfill_chatter_dates` — an on-demand per-record
backfill triggered from the form-view cogwheel. Scans messages where
`date == create_date` (no Date header preserved on import) and re-parses
the original Date from the stored body's quoted-reply header when possible.
"""

import logging
import re
from email.utils import parsedate_to_datetime

from odoo import _, models
from odoo.osv import expression

_logger = logging.getLogger(__name__)


# Patterns for extracting the original Date/Sent header from quoted email
# body content. Order matters — most specific first.
_DATE_PATTERNS = [
    re.compile(
        r"<b>\s*(?:Sent|Date|Envoyé\s*le)\s*:?\s*</b>\s*([^<\r\n]+?)\s*<",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:^|>)\s*(?:Sent|Date|Envoyé\s*le)\s*:\s*([^<\r\n]+?)(?:<|\r|\n)",
        re.IGNORECASE | re.MULTILINE,
    ),
]


def _parse_date_from_body(body):
    """Try to extract the original email Date from a quoted-reply block.

    Returns naive datetime (Odoo stores UTC-naive) or None.
    """
    if not body:
        return None
    for pattern in _DATE_PATTERNS:
        for m in pattern.finditer(body):
            candidate = m.group(1).strip()
            candidate = re.sub(r"\s*\([^)]+\)\s*$", "", candidate)
            if len(candidate) < 10:
                continue
            try:
                dt = parsedate_to_datetime(candidate)
                if dt is not None:
                    if dt.tzinfo is not None:
                        dt = dt.astimezone(tz=None).replace(tzinfo=None)
                    return dt
            except (TypeError, ValueError):
                continue
    return None


class MailMessage(models.Model):
    _inherit = "mail.message"
    _order = "date desc, id desc"

    def _auto_init(self):
        """Add a composite index aligned with the new `_order` so chatter
        fetches on large records don't fall back to a sequential scan.
        """
        res = super()._auto_init()
        self.env.cr.execute(
            """
            CREATE INDEX IF NOT EXISTS mail_message_model_res_id_date_id_idx
            ON mail_message (model, res_id, date DESC, id DESC)
            WHERE model IS NOT NULL AND res_id IS NOT NULL
            """
        )
        return res

    @staticmethod
    def _cursor_domain(msg, comparator):
        """Build a leaf-domain expressing `(date, id) <comparator> (msg.date, msg.id)`.

        comparator: '<', '<=', '>'. The chatter never asks for '>=' so we don't
        emit it.
        """
        d = msg.date
        i = msg.id
        if comparator == "<":
            return [
                "|",
                ("date", "<", d),
                "&", ("date", "=", d), ("id", "<", i),
            ]
        if comparator == "<=":
            return [
                "|",
                ("date", "<", d),
                "&", ("date", "=", d), ("id", "<=", i),
            ]
        if comparator == ">":
            return [
                "|",
                ("date", ">", d),
                "&", ("date", "=", d), ("id", ">", i),
            ]
        raise ValueError("Unknown comparator: %r" % (comparator,))

    def _message_fetch(self, domain, search_term=None, before=None,
                       after=None, around=None, limit=30):
        """Date-aware chatter pagination.

        Mirrors the upstream method (odoo/addons/mail/models/mail_message.py)
        but uses a `(date, id)` compound cursor instead of an `id`-only one.
        """
        Message = self.env["mail.message"]
        res = {}

        if search_term:
            search_term = search_term.replace(" ", "%")
            domain = expression.AND([
                domain,
                expression.OR([
                    [("attachment_ids", "in", self.env["ir.attachment"].sudo()._search(
                        [("name", "ilike", search_term)]
                    ))],
                    [("body", "ilike", search_term)],
                    [("subject", "ilike", search_term)],
                    [("subtype_id.description", "ilike", search_term)],
                ]),
            ])
            res["count"] = Message.search_count(domain)

        if around is not None:
            anchor = Message.browse(around).exists()
            if anchor:
                cd_before = self._cursor_domain(anchor, "<=")
                cd_after = self._cursor_domain(anchor, ">")
                messages_before = Message.search(
                    expression.AND([domain, cd_before]),
                    limit=limit // 2, order="date desc, id desc",
                )
                messages_after = Message.search(
                    expression.AND([domain, cd_after]),
                    limit=limit // 2, order="date asc, id asc",
                )
                combined = messages_after + messages_before
                res["messages"] = combined.sorted(
                    lambda m: (m.date or False, m.id), reverse=True,
                )
                return res

        if before:
            anchor = Message.browse(before).exists()
            if anchor:
                domain = expression.AND([domain, self._cursor_domain(anchor, "<")])
        if after:
            anchor = Message.browse(after).exists()
            if anchor:
                domain = expression.AND([domain, self._cursor_domain(anchor, ">")])

        messages = Message.search(
            domain,
            limit=limit,
            order="date asc, id asc" if after else "date desc, id desc",
        )
        if after:
            messages = messages.sorted(
                lambda m: (m.date or False, m.id), reverse=True,
            )

        res["messages"] = messages
        return res

    @staticmethod
    def _bf_chrono_neutral_context():
        return {
            "mail_create_nosubscribe": True,
            "mail_create_nolog": True,
            "mail_notify_force_send": False,
            "mail_auto_subscribe_no_notify": True,
            "tracking_disable": True,
        }

    def action_backfill_chatter_dates(self):
        """Per-record re-parse: take messages where date ≈ create_date and try
        to recover the original Date header from the quoted-reply block in
        the stored body. Triggered from the form-view cogwheel.

        Returns a display_notification action with the result count.
        """
        active_model = self.env.context.get("active_model")
        active_id = self.env.context.get("active_id")
        if not active_model or not active_id:
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("Réordonner par date"),
                    "message": _(
                        "Action invoquée hors du contexte d'un record — rien à "
                        "réordonner."
                    ),
                    "type": "warning",
                    "sticky": False,
                },
            }

        domain = [
            ("model", "=", active_model),
            ("res_id", "=", active_id),
            ("message_type", "=", "email"),
        ]
        candidates = self.sudo().search(domain)
        if not candidates:
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("Réordonner par date"),
                    "message": _("Aucun courriel à analyser sur ce record."),
                    "type": "info",
                    "sticky": False,
                },
            }

        examined = 0
        updated = 0
        skipped_backdated = 0
        parse_fail = 0

        # Guard against any side-effects: snapshot mail.mail count.
        Mail = self.env["mail.mail"].sudo()
        before_count = Mail.search_count([])

        for msg in candidates:
            if not msg.date or not msg.create_date:
                continue
            delta = abs((msg.create_date - msg.date).total_seconds())
            if delta >= 60:
                skipped_backdated += 1
                continue
            examined += 1
            parsed = _parse_date_from_body(msg.body or "")
            if not parsed:
                parse_fail += 1
                continue
            # Ignore trivial diffs (< 5 min).
            if abs((msg.date - parsed).total_seconds()) < 300:
                continue
            msg.with_context(**self._bf_chrono_neutral_context()).sudo().write({
                "date": parsed.strftime("%Y-%m-%d %H:%M:%S"),
            })
            updated += 1

        after_count = Mail.search_count([])
        if after_count != before_count:
            _logger.warning(
                "bf_chatter_chronological backfill leaked mail.mail records "
                "on %s#%s: %d -> %d",
                active_model, active_id, before_count, after_count,
            )

        if updated:
            msg_type = "success"
            message = _(
                "%(n)s message(s) re-daté(s) à partir de l'en-tête Date du body. "
                "Rafraîchis la page pour voir l'ordre mis à jour."
            ) % {"n": updated}
        elif parse_fail and not skipped_backdated:
            msg_type = "info"
            message = _(
                "%(n)s message(s) candidats mais aucune date trouvée dans le body — "
                "rien à re-dater."
            ) % {"n": parse_fail}
        else:
            msg_type = "info"
            message = _(
                "Rien à re-dater. %(b)s message(s) déjà backdaté(s), "
                "%(e)s candidat(s) examiné(s), %(f)s sans date parsable."
            ) % {"b": skipped_backdated, "e": examined, "f": parse_fail}

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Réordonner par date"),
                "message": message,
                "type": msg_type,
                "sticky": False,
            },
        }
