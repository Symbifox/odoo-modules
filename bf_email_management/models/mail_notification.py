"""mail.notification override — propagate read state to bf.email.

When a mail.notification's ``is_read`` flips to True, find any bf.email
row whose ``mail_message_id`` matches AND whose ``user_id`` is the user
behind the notification, then flip its ``status`` from ``new`` to ``read``.

This fixes the user-visible ambiguity: "read" used to mean only "opened
in the bf.email module" — now it means "the user (or chatter) saw it."
"""

import logging

from odoo import models

_logger = logging.getLogger(__name__)


class MailNotification(models.Model):
    _inherit = "mail.notification"

    def write(self, vals):
        result = super().write(vals)
        if vals.get("is_read") is True and self:
            try:
                self._propagate_read_to_bf_email()
            except Exception:
                _logger.warning(
                    "bf.email read propagation failed", exc_info=True,
                )
        return result

    def _propagate_read_to_bf_email(self):
        """Flip matching bf.email rows from status='new' to 'read'.

        Per-user: a notification whose ``res_partner_id`` belongs to an
        internal user only affects that user's own bf.email rows. Other
        users' rows with the same Message-ID are untouched.
        """
        # Build a partner -> internal user map for the notifications.
        partners = self.mapped("res_partner_id")
        if not partners:
            return
        user_by_partner = {}
        for p in partners:
            internal = p.user_ids.filtered(lambda u: not u.share)[:1]
            if internal:
                user_by_partner[p.id] = internal.id
        if not user_by_partner:
            return
        for partner_id, user_id in user_by_partner.items():
            relevant = self.filtered(
                lambda n: n.res_partner_id.id == partner_id and n.mail_message_id
            )
            if not relevant:
                continue
            message_ids = relevant.mapped("mail_message_id").ids
            rows = self.env["bf.email"].sudo().search([
                ("mail_message_id", "in", message_ids),
                ("user_id", "=", user_id),
                ("status", "=", "new"),
            ])
            if rows:
                rows.write({"status": "read"})
                _logger.info(
                    "bf.email: propagated chatter-read to %s rows (user %s)",
                    len(rows), user_id,
                )
