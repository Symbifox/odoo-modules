import logging

from odoo import models

_logger = logging.getLogger(__name__)


class BfEmail(models.Model):
    _inherit = 'bf.email'

    def write(self, vals):
        old_statuses = {rec.id: rec.status for rec in self}
        res = super().write(vals)
        if 'status' in vals:
            for rec in self:
                old = old_statuses.get(rec.id)
                new = rec.status
                if old == new:
                    continue
                if new == 'replied':
                    self._award_email_xp(rec, trigger='complete',
                                         description='Courriel répondu : %s' % (rec.subject or ''))
                elif new == 'archived':
                    self._award_email_xp(rec, trigger='write',
                                         description='Courriel archivé : %s' % (rec.subject or ''))
        return res

    def _award_email_xp(self, rec, trigger, description):
        if not self.env['ir.config_parameter'].sudo().get_param(
                'bf_gamification.gamification_enabled', 'True') == 'True':
            return
        try:
            user = rec.user_id or rec.create_uid
            if not user:
                return
            Profile = self.env['bf.gamification.profile']
            profile = Profile._get_or_create_profile(user)
            Rule = self.env['bf.gamification.xp.rule']
            rule = Rule.search([
                ('source', '=', 'email_triage'),
                ('trigger', '=', trigger),
                ('active', '=', True),
            ], limit=1)
            if rule:
                profile._award_xp(
                    rule.xp_amount, 'email_triage', description, reference=rec,
                )
        except Exception:
            _logger.warning("Fox Quest: erreur XP courriel", exc_info=True)
