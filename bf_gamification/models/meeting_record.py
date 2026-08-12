import logging

from odoo import api, models

_logger = logging.getLogger(__name__)


class MeetingRecord(models.Model):
    _inherit = 'meeting.record'

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for rec in records:
            self._award_meeting_xp(rec, trigger='create', source_value='meeting',
                                   description='Compte rendu créé : %s' % (rec.name or ''))
        return records

    def write(self, vals):
        old_states = {rec.id: rec.report_state for rec in self}
        res = super().write(vals)
        if 'report_state' in vals:
            for rec in self:
                old = old_states.get(rec.id)
                new = rec.report_state
                if old == new:
                    continue
                if new == 'reviewed':
                    self._award_meeting_xp(
                        rec, trigger='write', source_value='meeting',
                        description='Compte rendu révisé : %s' % (rec.name or ''),
                    )
                elif new == 'sent':
                    self._award_meeting_xp(
                        rec, trigger='complete', source_value='meeting',
                        description='Compte rendu envoyé : %s' % (rec.name or ''),
                    )
        return res

    def _award_meeting_xp(self, rec, trigger, source_value, description):
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
                ('source', '=', source_value),
                ('trigger', '=', trigger),
                ('active', '=', True),
            ], limit=1)
            if rule:
                profile._award_xp(
                    rule.xp_amount, source_value, description, reference=rec,
                )
        except Exception:
            _logger.warning("Fox Quest: erreur XP compte rendu", exc_info=True)
