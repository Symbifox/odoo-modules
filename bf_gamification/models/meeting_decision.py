import logging

from odoo import api, models

_logger = logging.getLogger(__name__)


class MeetingDecision(models.Model):
    _inherit = 'meeting.decision'

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for rec in records:
            self._award_decision_xp(rec)
        return records

    def _award_decision_xp(self, rec):
        if not self.env['ir.config_parameter'].sudo().get_param(
                'bf_gamification.gamification_enabled', 'True') == 'True':
            return
        try:
            user = rec.create_uid
            if not user:
                return
            Profile = self.env['bf.gamification.profile']
            profile = Profile._get_or_create_profile(user)
            Rule = self.env['bf.gamification.xp.rule']
            rule = Rule.search([
                ('source', '=', 'decision'),
                ('trigger', '=', 'create'),
                ('active', '=', True),
            ], limit=1)
            if rule:
                profile._award_xp(
                    rule.xp_amount, 'decision',
                    'Décision documentée : %s' % (rec.name or ''),
                    reference=rec,
                )
        except Exception:
            _logger.warning("Fox Quest: erreur XP décision", exc_info=True)
