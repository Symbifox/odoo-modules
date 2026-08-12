import logging

from odoo import api, models

_logger = logging.getLogger(__name__)


class ProjectTask(models.Model):
    _inherit = 'project.task'

    def write(self, vals):
        old_stages = {task.id: task.stage_id for task in self}
        res = super().write(vals)
        if 'stage_id' in vals:
            for task in self:
                self._check_task_completion_xp(task, old_stages.get(task.id))
        return res

    def _check_task_completion_xp(self, task, old_stage):
        """Award XP when task moves to a done/completed stage."""
        if not self.env['ir.config_parameter'].sudo().get_param(
                'bf_gamification.gamification_enabled', 'True') == 'True':
            return

        try:
            new_stage = task.stage_id
            if not new_stage or not new_stage.fold:
                return
            if old_stage and old_stage.fold:
                return  # Was already in a done stage

            user = task.user_ids[:1] if task.user_ids else task.create_uid
            if not user:
                return

            Profile = self.env['bf.gamification.profile']
            profile = Profile._get_or_create_profile(user)
            Rule = self.env['bf.gamification.xp.rule']

            # Basic task completion
            complete_rule = Rule.search([
                ('source', '=', 'task'),
                ('trigger', '=', 'complete'),
                ('active', '=', True),
                ('min_value', '=', 0),
            ], limit=1)
            if complete_rule:
                profile._award_xp(
                    complete_rule.xp_amount, 'task',
                    'Tâche complétée : %s' % task.name,
                    reference=task,
                )

            # Bonus for completing before deadline
            if task.date_deadline:
                from datetime import date
                if date.today() <= task.date_deadline:
                    early_rule = Rule.search([
                        ('source', '=', 'task'),
                        ('trigger', '=', 'complete'),
                        ('active', '=', True),
                        ('min_value', '>', 0),
                    ], limit=1)
                    if early_rule:
                        profile._award_xp(
                            early_rule.xp_amount, 'task',
                            'Tâche complétée avant échéance : %s' % task.name,
                            reference=task,
                        )
        except Exception:
            _logger.warning("Fox Quest: erreur lors de l'attribution XP tâche", exc_info=True)
