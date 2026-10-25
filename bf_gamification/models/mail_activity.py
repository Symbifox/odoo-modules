from odoo import models

from . import fq_days, fq_savepoint


class MailActivity(models.Model):
    _inherit = 'mail.activity'

    def _action_done(self, feedback=False, attachment_ids=None):
        """Award XP when a scheduled activity is marked as done."""
        # Capture info before super() unlinks the records
        users_activities = []
        actor = self.env.user
        own_due = False
        for activity in self:
            if activity.user_id:
                summary = activity.summary or (
                    activity.activity_type_id.name
                    if activity.activity_type_id else 'Activité'
                )
                user = activity.user_id
                # À l'échéance : faite au plus tard le jour dû, dans le fuseau
                # de la personne. Seulement une activité planifiée la
                # veille ou avant : créer et clore la sienne le même jour ne
                # prouve aucune ponctualité.
                today = fq_days.user_today(user)
                on_time = bool(activity.date_deadline and today <= activity.date_deadline
                               and fq_days.local_date(user, activity.create_date) < today)
                users_activities.append((user, summary, on_time))
                if user == actor and activity.date_deadline \
                        and activity.date_deadline <= fq_days.user_today(user):
                    own_due = True

        res = super()._action_done(feedback=feedback, attachment_ids=attachment_ids)

        if not self.env['ir.config_parameter'].sudo().get_param(
                'bf_gamification.gamification_enabled', 'True') == 'True':
            return res

        Profile = self.env['bf.gamification.profile']
        for user, summary, on_time in users_activities:
            with fq_savepoint.tolerated("XP activité"):
                profile = Profile._get_or_create_profile(user)
                Rule = self.env['bf.gamification.xp.rule']

                rule = Rule.search([
                    ('source', '=', 'activity'),
                    ('trigger', '=', 'complete'),
                    ('active', '=', True),
                ], limit=1)
                if rule:
                    profile._award_xp(
                        rule.xp_amount, 'activity',
                        'Activité complétée : %s' % summary,
                    )
                # Le bonus « à temps » va à qui fait sa propre activité : une
                # activité close par un tiers ou un robot ne prouve rien.
                if on_time and user == self.env.user:
                    profile._bf_award_rule(
                        'bf_gamification.xp_rule_on_time_activity', 'on_time',
                        'Activité faite à l’échéance : %s' % summary)

        # La personne vient de faire une de SES activités dues : sa liste est-elle vide ?
        if own_due:
            Profile._bf_caught_up_check(actor, 'activity')
        return res
