import logging

from odoo import models

_logger = logging.getLogger(__name__)


class MailActivity(models.Model):
    _inherit = 'mail.activity'

    def _action_done(self, feedback=False, attachment_ids=None):
        """Award XP when a scheduled activity is marked as done."""
        # Capture info before super() unlinks the records
        users_activities = []
        for activity in self:
            if activity.user_id:
                summary = activity.summary or (
                    activity.activity_type_id.name
                    if activity.activity_type_id else 'Activit\u00e9'
                )
                users_activities.append((activity.user_id, summary))

        res = super()._action_done(feedback=feedback, attachment_ids=attachment_ids)

        if not self.env['ir.config_parameter'].sudo().get_param(
                'bf_gamification.gamification_enabled', 'True') == 'True':
            return res

        for user, summary in users_activities:
            try:
                Profile = self.env['bf.gamification.profile']
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
                        'Activit\u00e9 compl\u00e9t\u00e9e : %s' % summary,
                    )
            except Exception:
                _logger.warning("Fox Quest: erreur XP activit\u00e9", exc_info=True)

        return res
