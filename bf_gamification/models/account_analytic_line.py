from datetime import timedelta

from odoo import api, models

from . import fq_days, fq_savepoint


class AccountAnalyticLine(models.Model):
    _inherit = 'account.analytic.line'

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        for line in lines:
            if line.project_id and line.unit_amount > 0:
                self._award_timesheet_xp(line)
        return lines

    def _award_timesheet_xp(self, line):
        """Award XP for timesheet entry based on active rules."""
        if not self.env['ir.config_parameter'].sudo().get_param(
                'bf_gamification.gamification_enabled', 'True') == 'True':
            return

        with fq_savepoint.tolerated("XP feuille de temps"):
            Profile = self.env['bf.gamification.profile']
            profile = Profile._get_or_create_profile(line.user_id or line.create_uid)

            # Find hourly XP rule
            Rule = self.env['bf.gamification.xp.rule']
            hourly_rule = Rule.search([
                ('source', '=', 'timesheet'),
                ('trigger', '=', 'create'),
                ('active', '=', True),
            ], limit=1)
            if hourly_rule:
                xp = int(line.unit_amount * hourly_rule.xp_amount)
                if xp > 0:
                    profile._award_xp(
                        xp, 'timesheet',
                        '%.1fh de feuille de temps' % line.unit_amount,
                        reference=line,
                    )

            # Check daily productive bonus
            daily_rule = Rule.search([
                ('source', '=', 'timesheet'),
                ('trigger', '=', 'daily'),
                ('active', '=', True),
            ], limit=1)
            if daily_rule and daily_rule.min_value > 0:
                # Le jour de la personne, pas celui du serveur.
                worker = line.user_id or line.create_uid
                today = fq_days.user_today(worker)
                daily_hours = sum(self.search([
                    ('user_id', '=', worker.id),
                    ('date', '=', today),
                    ('project_id', '!=', False),
                ]).mapped('unit_amount'))
                if daily_hours >= daily_rule.min_value:
                    # Check if bonus already given today
                    existing = self.env['bf.gamification.xp.transaction'].search([
                        ('user_id', '=', worker.id),
                        ('source', '=', 'timesheet'),
                        ('description', 'like', 'Journée productive'),
                        ('date', '>=', fq_days.local_midnight_utc(worker, today)),
                        ('date', '<', fq_days.local_midnight_utc(worker, today + timedelta(days=1))),
                    ], limit=1)
                    if not existing:
                        profile._award_xp(
                            daily_rule.xp_amount, 'timesheet',
                            'Journée productive (%.1fh)' % daily_hours,
                        )
