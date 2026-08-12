import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    gamification_enabled = fields.Boolean(
        string="Activer Fox Quest",
        config_parameter='bf_gamification.gamification_enabled',
        default=True,
    )
    gamification_show_leaderboard = fields.Boolean(
        string="Afficher le classement",
        config_parameter='bf_gamification.show_leaderboard',
        default=True,
    )
    gamification_popup_enabled = fields.Boolean(
        string="Afficher les popups",
        config_parameter='bf_gamification.popup_enabled',
        default=True,
    )
    gamification_sound_enabled = fields.Boolean(
        string="Activer les sons",
        config_parameter='bf_gamification.sound_enabled',
        default=True,
    )
    gamification_streak_reset_days = fields.Integer(
        string="Jours avant reset du streak",
        config_parameter='bf_gamification.streak_reset_days',
        default=2,
    )
    gamification_confetti_enabled = fields.Boolean(
        string="Activer les confettis",
        config_parameter='bf_gamification.confetti_enabled',
        default=True,
    )

    def action_reset_all_progress(self):
        """Wipe all XP, badges, streaks, and reward claims for every user."""
        self.ensure_one()
        cr = self.env.cr
        cr.execute("DELETE FROM bf_gamification_xp_transaction")
        xp_count = cr.rowcount
        cr.execute("DELETE FROM bf_gamification_user_badge")
        badge_count = cr.rowcount
        cr.execute("DELETE FROM bf_gamification_reward_claim")
        claim_count = cr.rowcount
        cr.execute("DELETE FROM bf_gamification_profile_showcase_rel")
        cr.execute("""
            UPDATE bf_gamification_profile
            SET total_xp = 0,
                current_streak = 0,
                longest_streak = 0,
                last_activity_date = NULL,
                level_id = NULL,
                xp_to_next_level = 0,
                progress_percent = 0,
                title = NULL
        """)
        profile_count = cr.rowcount
        _logger.info(
            "Fox Quest reset: %d XP transactions, %d badges, %d claims deleted; %d profiles zeroed",
            xp_count, badge_count, claim_count, profile_count,
        )
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Fox Quest',
                'message': '%d profils réinitialisés.' % profile_count,
                'type': 'success',
                'sticky': False,
            },
        }
