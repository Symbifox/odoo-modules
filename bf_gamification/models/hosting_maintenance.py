from odoo import api, models

from . import fq_savepoint


class HostingMaintenanceSchedule(models.Model):
    _inherit = 'hosting.maintenance.schedule'

    def write(self, vals):
        res = super().write(vals)
        if 'last_performed' in vals:
            for schedule in self:
                self._award_maintenance_xp(schedule)
        return res

    def _award_maintenance_xp(self, schedule):
        """Award XP when maintenance is marked as done."""
        if not self.env['ir.config_parameter'].sudo().get_param(
                'bf_gamification.gamification_enabled', 'True') == 'True':
            return

        with fq_savepoint.tolerated("XP maintenance"):
            Profile = self.env['bf.gamification.profile']
            profile = Profile._get_or_create_profile(self.env.user)
            Rule = self.env['bf.gamification.xp.rule']

            rule = Rule.search([
                ('source', '=', 'hosting'),
                ('trigger', '=', 'complete'),
                ('active', '=', True),
            ], limit=1)
            if rule:
                profile._award_xp(
                    rule.xp_amount, 'hosting',
                    'Maintenance complétée : %s' % schedule.display_name,
                )
