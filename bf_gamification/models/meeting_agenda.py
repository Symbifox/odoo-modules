import logging

from odoo import api, models

_logger = logging.getLogger(__name__)


class MeetingAgenda(models.Model):
    _inherit = 'meeting.agenda'

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for rec in records:
            self._award_agenda_xp(
                rec, trigger='create',
                description='Ordre du jour créé : %s' % (rec.name or ''),
            )
        return records

    def write(self, vals):
        old_states = {rec.id: rec.state for rec in self}
        res = super().write(vals)
        if 'state' in vals:
            for rec in self:
                old = old_states.get(rec.id)
                if old != rec.state and rec.state == 'confirmed':
                    self._award_agenda_xp(
                        rec, trigger='complete',
                        description='OdJ confirmé : %s' % (rec.name or ''),
                    )
        return res

    def _award_agenda_xp(self, rec, trigger, description):
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
                ('source', '=', 'agenda'),
                ('trigger', '=', trigger),
                ('active', '=', True),
            ], limit=1)
            if rule:
                profile._award_xp(
                    rule.xp_amount, 'agenda', description, reference=rec,
                )
        except Exception:
            _logger.warning("Fox Quest: erreur XP ordre du jour", exc_info=True)
