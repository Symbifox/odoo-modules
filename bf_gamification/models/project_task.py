from odoo import models

from . import fq_days, fq_savepoint

CLOSED_STATES = ('1_done', '1_canceled')


class ProjectTask(models.Model):
    _inherit = 'project.task'

    def _bf_fq_is_done(self):
        """Fermée : étape repliée, ou état « fait ». Un projet à une
        seule étape ferme ses tâches par l'état seulement."""
        self.ensure_one()
        return bool((self.stage_id and self.stage_id.fold) or self.state == '1_done')

    def _bf_fq_overdue_for(self, user):
        """Les tâches de `self` qui sont en retard pour `user`, dans son fuseau.
        Une tâche dans une étape repliée est fermée, même si son état n'a pas
        suivi."""
        today = fq_days.user_today(user)
        return self.filtered(
            lambda t: user in t.user_ids and t.state not in CLOSED_STATES and t.active
            and not (t.stage_id and t.stage_id.fold)
            and t.date_deadline and fq_days.local_date(user, t.date_deadline) < today)

    def write(self, vals):
        watched = {'stage_id', 'state', 'date_deadline', 'user_ids', 'active'} & set(vals)
        before = {task.id: task._bf_fq_is_done() for task in self} if watched else {}
        # La liste « en retard » de qui agit ne se fête que si ce geste-ci en
        # retire une tâche : fermer la tâche d'un collègue ne fait rien.
        overdue = self._bf_fq_overdue_for(self.env.user) if watched else self.browse()
        res = super().write(vals)
        if watched:
            closed = self.filtered(lambda t: not before.get(t.id) and t._bf_fq_is_done())
            for task in closed:
                self._check_task_completion_xp(task)
            # Seulement en FERMANT une tâche en retard : repousser
            # l'échéance ou se retirer de la tâche vide aussi la liste, sans
            # rien avoir fait.
            if overdue & closed:
                self.env['bf.gamification.profile']._bf_caught_up_check(self.env.user, 'task')
        return res

    def _check_task_completion_xp(self, task, old_stage=None):
        """Award XP when a task gets closed (stage folded or state done)."""
        if not self.env['ir.config_parameter'].sudo().get_param(
                'bf_gamification.gamification_enabled', 'True') == 'True':
            return

        with fq_savepoint.tolerated("XP tâche"):
            if old_stage and old_stage.fold:
                return  # Was already in a done stage

            Profile = self.env['bf.gamification.profile']
            user = task.user_ids[:1] if task.user_ids else task.create_uid
            if not Profile._bf_is_player(user):
                user = self.env.user
            if not user or not Profile._bf_is_player(user):
                return

            # Une tâche ne paie qu'une fois : la rouvrir puis la refermer ne
            # rapporte rien de plus.
            Txn = self.env['bf.gamification.xp.transaction'].sudo()
            if Txn.search_count([('source', '=', 'task'),
                                 ('reference', '=', 'project.task,%s' % task.id)]):
                return

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

            # Fermée au plus tard le jour de son échéance, dans le fuseau de la
            # personne. Avant la 18.0.3.0.0, `date.today() <= date_deadline` comparait
            # une date à un datetime : TypeError avalé par le `except`, et le
            # bonus n'a jamais été versé. Comme pour
            # les activités, seulement une tâche créée la veille ou avant : créer
            # et fermer la sienne le même jour ne prouve aucune ponctualité.
            today = fq_days.user_today(user)
            if task.date_deadline and fq_days.local_date(user, task.create_date) < today:
                if today <= fq_days.local_date(user, task.date_deadline):
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
