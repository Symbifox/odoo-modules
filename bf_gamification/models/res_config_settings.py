import logging

from odoo import _, api, fields, models
from odoo.exceptions import AccessError

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
        string="Jours ouvrables avant reset du streak",
        config_parameter='bf_gamification.streak_reset_days',
        default=2,
    )
    # Congés de constance : éteints par défaut ; chaque employé choisit
    # ensuite d'y participer depuis son tableau de bord.
    gamification_leave_enabled = fields.Boolean(
        string="Jours de congé de constance",
        config_parameter='bf_gamification.leave_enabled',
        default=False,
    )
    # Odoo ne garde pas un 0 dans un paramètre : `set_values` retire le paramètre
    # et le défaut revient. « 0 = sans plafond » était donc faux :
    # saisir 0 remettait 5. Les trois aides le disent.
    gamification_leave_streak_days = fields.Integer(
        string="Palier (jours ouvrables de constance)",
        config_parameter='bf_gamification.leave_streak_days',
        default=60,
        help="Jours ouvrables de constance avant qu'une demande de congé s'ouvre. "
             "60 par défaut ; saisir 0 ou vider le champ remet 60.",
    )
    gamification_leave_days_per_step = fields.Float(
        string="Jours de congé par palier",
        config_parameter='bf_gamification.leave_days_per_step',
        default=1.0,
        help="Jours demandés à chaque palier atteint. 1 par défaut ; saisir 0 ou "
             "vider le champ remet 1.",
    )
    gamification_leave_max_per_year = fields.Integer(
        string="Plafond par année civile",
        config_parameter='bf_gamification.leave_max_per_year',
        default=5,
        help="Nombre maximal de demandes de congé de constance par personne et par "
             "année civile. 5 par défaut ; saisir 0 ou vider le champ remet 5 : il "
             "n'y a pas de réglage « sans plafond », pour ne pas limiter saisir une "
             "grande valeur (999).",
    )
    gamification_confetti_enabled = fields.Boolean(
        string="Activer les confettis",
        config_parameter='bf_gamification.confetti_enabled',
        default=True,
    )

    def action_reset_all_progress(self):
        """Wipe all XP, badges, streaks, and reward claims for every user
        (constance time-off requests are kept)."""
        self.ensure_one()
        # SQL brut, qui ne passe par aucune règle d'accès : la page des réglages
        # n'est ouverte qu'aux administrateurs, mais une méthode publique se joint
        # aussi par RPC ou depuis du code en sudo. On le redit ici.
        if not self.env.user.has_group("base.group_system"):
            raise AccessError(_("Seul un administrateur peut remettre à zéro la progression."))
        self.env.flush_all()
        cr = self.env.cr
        cr.execute("DELETE FROM bf_gamification_xp_transaction")
        xp_count = cr.rowcount
        cr.execute("DELETE FROM bf_gamification_user_badge")
        badge_count = cr.rowcount
        # Un congé de constance est une décision sur des jours de
        # congé, pas un jeu : sans Congés, la demande approuvée en est la seule
        # trace. La remise à zéro les garde ; les autres réclamations partent,
        # avec leurs activités (elles restaient orphelines).
        cr.execute("""
            DELETE FROM bf_gamification_reward_claim
             WHERE reward_id NOT IN (SELECT id FROM bf_gamification_reward
                                      WHERE is_constance_leave IS TRUE)
            RETURNING id
        """)
        claim_ids = [row[0] for row in cr.fetchall()]
        claim_count = len(claim_ids)
        if claim_ids:
            cr.execute("DELETE FROM mail_activity WHERE res_model = %s AND res_id = ANY(%s)",
                       ('bf.gamification.reward.claim', claim_ids))
        cr.execute("DELETE FROM bf_gamification_profile_showcase_rel")
        # Les journées « à jour » et la marche vers le prochain congé
        # repartent aussi ; le choix de participer, lui, appartient à la personne.
        cr.execute("DELETE FROM bf_gamification_caught_up")
        cr.execute("""
            UPDATE bf_gamification_profile
            SET total_xp = 0,
                xp_balance = 0,
                current_streak = 0,
                longest_streak = 0,
                last_activity_date = NULL,
                level_id = NULL,
                xp_to_next_level = 0,
                progress_percent = 0,
                title = NULL,
                leave_progress = 0
        """)
        profile_count = cr.rowcount
        self.env.invalidate_all(flush=False)
        _logger.info(
            "Fox Quest reset: %d XP transactions, %d badges, %d claims deleted "
            "(constance time off kept); %d profiles zeroed",
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
