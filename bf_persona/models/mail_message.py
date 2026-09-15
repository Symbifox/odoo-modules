"""mail.message hook: keep persona.last_interaction_date fresh on each email.

Runs after `super().create()` so failures here cannot break chatter posting.
It only writes a date. The measured facts (messages each way, messages left
unanswered, length of ours) are refreshed by the daily cron.
"""
import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class MailMessage(models.Model):
    _inherit = "mail.message"

    @api.model_create_multi
    def create(self, vals_list):
        messages = super().create(vals_list)
        try:
            messages._bf_persona_log_interaction()
        except Exception as e:
            _logger.warning("bf_persona interaction hook failed: %s", e)
        return messages

    def _bf_persona_log_interaction(self):
        partner_ids = set()
        for msg in self:
            if msg.message_type not in ("email", "comment", "email_outgoing"):
                continue
            if msg.subtype_id and msg.subtype_id.internal:
                continue
            if msg.message_type == "comment" and not msg.partner_ids:
                continue
            # A persona is kept on a person, sometimes on a company: touch both.
            for partner in (msg.author_id | msg.partner_ids):
                partner_ids.add(partner.id)
                if partner.commercial_partner_id:
                    partner_ids.add(partner.commercial_partner_id.id)
        if not partner_ids:
            return
        today = fields.Date.context_today(self)
        personas = self.env["contact.persona"].sudo().search([
            ("partner_id", "in", list(partner_ids)),
            "|", ("last_interaction_date", "=", False), ("last_interaction_date", "<", today),
        ])
        if personas:
            # Full silence: no chatter, tracking or notification on the persona.
            personas.with_context(
                tracking_disable=True,
                mail_create_nosubscribe=True,
                mail_post_autofollow=False,
                mail_notify_force_send=False,
            ).write({"last_interaction_date": today})
