import logging

from odoo import api, models

_logger = logging.getLogger(__name__)


class MailMessage(models.Model):
    _inherit = 'mail.message'

    @api.model_create_multi
    def create(self, vals_list):
        messages = super().create(vals_list)
        for msg in messages:
            if msg.message_type == 'comment' and msg.body and msg.author_id:
                self._award_message_xp(msg)
        return messages

    def _award_message_xp(self, msg):
        """Award XP for posting a message or internal note in the chatter."""
        if not self.env['ir.config_parameter'].sudo().get_param(
                'bf_gamification.gamification_enabled', 'True') == 'True':
            return

        try:
            user = self.env['res.users'].sudo().search([
                ('partner_id', '=', msg.author_id.id),
                ('active', '=', True),
            ], limit=1)
            if not user:
                return

            Profile = self.env['bf.gamification.profile']
            profile = Profile._get_or_create_profile(user)
            Rule = self.env['bf.gamification.xp.rule']

            rule = Rule.search([
                ('source', '=', 'message'),
                ('trigger', '=', 'create'),
                ('active', '=', True),
            ], limit=1)
            if rule:
                if msg.subtype_id and msg.subtype_id.internal:
                    desc = 'Note interne post\u00e9e'
                else:
                    desc = 'Message post\u00e9'
                profile._award_xp(
                    rule.xp_amount, 'message',
                    desc,
                    reference=msg,
                )
        except Exception:
            _logger.warning("Fox Quest: erreur XP message", exc_info=True)
