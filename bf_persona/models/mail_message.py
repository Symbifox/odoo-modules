"""mail.message hook: keep persona.last_interaction_date fresh on each email.

Runs after `super().create()` so failures here cannot break chatter posting.
The hook is intentionally lightweight: it only writes a Date and (at most
once per day, per persona) inserts a `Dernière interaction` KPI row.
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
        Persona = self.env["contact.persona"].sudo()
        Kpi = self.env["contact.persona.kpi"].sudo()
        today = fields.Date.context_today(self)
        # Map partner_id → persona to update once per partner regardless of
        # how many messages we just batched.
        partner_to_personas = {}
        for msg in self:
            if msg.message_type != "email":
                continue
            if getattr(msg, "is_internal", False):
                continue
            partner_ids = set()
            if msg.author_id and msg.author_id.commercial_partner_id:
                partner_ids.add(msg.author_id.commercial_partner_id.id)
            for p in msg.partner_ids:
                if p.commercial_partner_id:
                    partner_ids.add(p.commercial_partner_id.id)
            if not partner_ids:
                continue
            for pid in partner_ids:
                partner_to_personas.setdefault(pid, set()).add(msg.id)
        if not partner_to_personas:
            return
        personas = Persona.search([
            ("partner_id", "in", list(partner_to_personas.keys())),
        ])
        if not personas:
            return
        # Wrap every IO in full silence to keep this hook from emitting any
        # chatter / tracking / notification mails on the persona record.
        silence = dict(
            tracking_disable=True,
            mail_create_nosubscribe=True,
            mail_post_autofollow=False,
            mail_notify_force_send=False,
        )
        for persona in personas:
            if not persona.last_interaction_date or persona.last_interaction_date < today:
                persona.with_context(**silence).write({"last_interaction_date": today})
            # Idempotent KPI: skip if a 'Dernière interaction' row already exists for today.
            existing = Kpi.search([
                ("persona_id", "=", persona.id),
                ("name", "=", "Dernière interaction"),
                ("date_measured", "=", today),
            ], limit=1)
            if not existing:
                Kpi.create({
                    "persona_id": persona.id,
                    "name": "Dernière interaction",
                    "value_text": today.isoformat(),
                    "date_measured": today,
                    "source": "email_management",
                })
