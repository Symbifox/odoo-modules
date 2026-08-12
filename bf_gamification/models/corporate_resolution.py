import logging

from odoo import models

_logger = logging.getLogger(__name__)


class CorporateResolution(models.Model):
    _inherit = 'corporate.resolution'

    def write(self, vals):
        old_statuses = {rec.id: rec.status for rec in self}
        res = super().write(vals)
        if 'status' in vals:
            for rec in self:
                old = old_statuses.get(rec.id)
                if old != 'adopted' and rec.status == 'adopted':
                    self._award_resolution_xp(rec)
        return res

    def _award_resolution_xp(self, rec):
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
                ('source', '=', 'resolution'),
                ('trigger', '=', 'complete'),
                ('active', '=', True),
            ], limit=1)
            if rule:
                profile._award_xp(
                    rule.xp_amount, 'resolution',
                    'Résolution adoptée : %s' % (rec.name or rec.sequence or ''),
                    reference=rec,
                )
        except Exception:
            _logger.warning("Fox Quest: erreur XP résolution", exc_info=True)
