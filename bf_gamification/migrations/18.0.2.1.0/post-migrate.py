"""Fox Quest v2.1.0 — Cohesion overhaul.

- Smooth level XP curve + fix theme inconsistencies (Louveteau→Goupil, Kitsune title)
- Rebalance XP rules (streak down, core work up)
- Convert manual badges to automatic where hooks exist
- Add 13 new badges filling progression gaps + 2 legendary tiers
- Add streak and maintenance badge support via new threshold_field/condition_user_field
- Full progress reset: all XP, badges, streaks, and reward claims wiped for fresh start
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    _reset_all_progress(env)
    _update_levels(env)
    _update_xp_rules(env)
    _update_existing_badges(env)
    _create_new_badges(env)
    _logger.info("Fox Quest v2.1.0 migration completed")


# ── Full reset ──────────────────────────────────────────────────────────

def _reset_all_progress(env):
    """Wipe all player progress for a clean start with the new economy."""
    cr = env.cr

    # Delete all XP transactions
    cr.execute("DELETE FROM bf_gamification_xp_transaction")
    _logger.info("Reset: deleted all XP transactions (%d rows)", cr.rowcount)

    # Delete all earned badges
    cr.execute("DELETE FROM bf_gamification_user_badge")
    _logger.info("Reset: deleted all user badges (%d rows)", cr.rowcount)

    # Delete all reward claims
    cr.execute("DELETE FROM bf_gamification_reward_claim")
    _logger.info("Reset: deleted all reward claims (%d rows)", cr.rowcount)

    # Clear showcase badge links
    cr.execute("DELETE FROM bf_gamification_profile_showcase_rel")

    # Reset all profiles (keep the profile records, just zero out progress)
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
    _logger.info("Reset: zeroed %d player profiles", cr.rowcount)


# ── Levels ──────────────────────────────────────────────────────────────

def _update_levels(env):
    """Smooth XP curve + fix theme inconsistencies."""
    updates = {
        'bf_gamification.level_bronze': {
            'title': 'Goupil',  # was "Louveteau" (wolf cub — wrong theme)
        },
        'bf_gamification.level_argent_2': {
            'min_xp': 700,  # was 750
        },
        'bf_gamification.level_or': {
            'min_xp': 1200,  # was 1500
        },
        'bf_gamification.level_or_2': {
            'min_xp': 2000,  # was 3000
        },
        'bf_gamification.level_platine': {
            'min_xp': 3500,  # was 5000
        },
        'bf_gamification.level_platine_2': {
            'min_xp': 6000,  # was 8000
        },
        'bf_gamification.level_diamant': {
            'min_xp': 10000,  # was 12000
        },
        'bf_gamification.level_diamant_2': {
            'min_xp': 15000,  # was 20000
            'title': 'Ma\u00eetre Renard',  # was duplicate "Kitsune"
        },
    }
    for xml_id, vals in updates.items():
        record = env.ref(xml_id, raise_if_not_found=False)
        if record:
            record.write(vals)
            _logger.info("Level %s updated: %s", xml_id, vals)


# ── XP Rules ────────────────────────────────────────────────────────────

def _update_xp_rules(env):
    """Rebalance XP: core work up, streak down."""
    updates = {
        'bf_gamification.xp_rule_timesheet_hourly': {'xp_amount': 2},   # was 1
        'bf_gamification.xp_rule_timesheet_daily': {'xp_amount': 8},    # was 5
        'bf_gamification.xp_rule_streak': {'xp_amount': 3},             # was 10
        'bf_gamification.xp_rule_task_complete': {'xp_amount': 8},      # was 5
        'bf_gamification.xp_rule_task_early': {'xp_amount': 12},        # was 10
        'bf_gamification.xp_rule_maintenance': {'xp_amount': 3},        # was 2
        'bf_gamification.xp_rule_ticket_resolve': {'xp_amount': 5},     # was 3
        'bf_gamification.xp_rule_activity_done': {'xp_amount': 2},      # was 1
    }
    for xml_id, vals in updates.items():
        record = env.ref(xml_id, raise_if_not_found=False)
        if record:
            record.write(vals)
            _logger.info("XP rule %s updated: %s", xml_id, vals)


# ── Existing badges ────────────────────────────────────────────────────

def _update_existing_badges(env):
    """Convert manual→automatic and fix streak badges to threshold."""
    Badge = env['bf.gamification.badge']

    # Tasks: manual → automatic with user_ids field
    task_badges = {
        'bf_gamification.badge_first_task': {
            'condition_type': 'automatic',
            'condition_model': 'project.task',
            'condition_domain': "[('stage_id.fold', '=', True)]",
            'condition_threshold': 1,
            'condition_user_field': 'user_ids',
        },
        'bf_gamification.badge_50_tasks': {
            'condition_type': 'automatic',
            'condition_model': 'project.task',
            'condition_domain': "[('stage_id.fold', '=', True)]",
            'condition_threshold': 50,
            'condition_user_field': 'user_ids',
        },
    }
    for xml_id, vals in task_badges.items():
        record = env.ref(xml_id, raise_if_not_found=False)
        if record:
            record.write(vals)
            _logger.info("Badge %s → automatic (tasks)", xml_id)

    # Helpdesk: manual → automatic with user_id field
    ticket_badges = {
        'bf_gamification.badge_first_ticket': {
            'condition_type': 'automatic',
            'condition_model': 'helpdesk.ticket',
            'condition_domain': "[('stage_id.fold', '=', True)]",
            'condition_threshold': 1,
            'condition_user_field': 'user_id',
        },
        'bf_gamification.badge_25_tickets': {
            'condition_type': 'automatic',
            'condition_model': 'helpdesk.ticket',
            'condition_domain': "[('stage_id.fold', '=', True)]",
            'condition_threshold': 25,
            'condition_user_field': 'user_id',
        },
    }
    for xml_id, vals in ticket_badges.items():
        record = env.ref(xml_id, raise_if_not_found=False)
        if record:
            record.write(vals)
            _logger.info("Badge %s → automatic (helpdesk)", xml_id)

    # Maintenance: manual → automatic with write_uid field
    maint_badge = env.ref('bf_gamification.badge_first_maintenance', raise_if_not_found=False)
    if maint_badge:
        maint_badge.write({
            'condition_type': 'automatic',
            'condition_model': 'hosting.maintenance.schedule',
            'condition_domain': "[('last_performed_date', '!=', False)]",
            'condition_threshold': 1,
            'condition_user_field': 'write_uid',
        })
        _logger.info("Badge badge_first_maintenance → automatic (hosting)")

    # Streaks: manual → threshold with longest_streak field
    streak_badges = {
        'bf_gamification.badge_streak_7': {
            'condition_type': 'threshold',
            'threshold_field': 'longest_streak',
            'condition_threshold': 7,
            # Clear manual-only fields
            'condition_model': False,
            'condition_domain': False,
        },
        'bf_gamification.badge_streak_30': {
            'condition_type': 'threshold',
            'threshold_field': 'longest_streak',
            'condition_threshold': 30,
            'condition_model': False,
            'condition_domain': False,
        },
    }
    for xml_id, vals in streak_badges.items():
        record = env.ref(xml_id, raise_if_not_found=False)
        if record:
            record.write(vals)
            _logger.info("Badge %s → threshold (longest_streak)", xml_id)

    # Fix badge_100_hours description (said "100 hours" but checks 100 XP)
    badge_100 = env.ref('bf_gamification.badge_100_hours', raise_if_not_found=False)
    if badge_100:
        badge_100.write({
            'description': 'Atteindre 100 XP \u2014 les premiers pas comptent',
        })


# ── New badges ──────────────────────────────────────────────────────────

def _create_new_badges(env):
    """Create badges that fill progression gaps."""
    Badge = env['bf.gamification.badge']
    IrModelData = env['ir.model.data']

    def _get_or_create(xml_id_suffix, vals):
        """Create badge + ir.model.data entry if it doesn't exist."""
        full_xml_id = 'bf_gamification.%s' % xml_id_suffix
        existing = env.ref(full_xml_id, raise_if_not_found=False)
        if existing:
            _logger.info("Badge %s already exists, skipping", full_xml_id)
            return existing
        badge = Badge.create(vals)
        IrModelData.create({
            'module': 'bf_gamification',
            'name': xml_id_suffix,
            'model': 'bf.gamification.badge',
            'res_id': badge.id,
            'noupdate': True,
        })
        _logger.info("Badge %s created (id=%s)", full_xml_id, badge.id)
        return badge

    # Category references
    cat_teamwork = env.ref('bf_gamification.badge_cat_teamwork').id
    cat_hosting = env.ref('bf_gamification.badge_cat_hosting').id
    cat_knowledge = env.ref('bf_gamification.badge_cat_knowledge').id
    cat_communication = env.ref('bf_gamification.badge_cat_communication').id
    cat_milestone = env.ref('bf_gamification.badge_cat_milestone').id
    cat_productivity = env.ref('bf_gamification.badge_cat_productivity').id

    # ── Tasks: 10 tasks ──
    _get_or_create('badge_10_tasks', {
        'name': 'Chasseur Agile',
        'description': 'Compl\u00e9ter 10 t\u00e2ches \u2014 vos pattes s\'aiguisent',
        'category_id': cat_teamwork,
        'xp_reward': 20,
        'rarity': 'uncommon',
        'condition_type': 'automatic',
        'condition_model': 'project.task',
        'condition_domain': "[('stage_id.fold', '=', True)]",
        'condition_threshold': 10,
        'condition_user_field': 'user_ids',
        'popup_effect': 'confetti',
        'popup_message': 'Vos griffes ne manquent jamais leur cible !',
    })

    # ── Tasks: 100 tasks ──
    _get_or_create('badge_100_tasks', {
        'name': 'Chasseur Expert',
        'description': 'Compl\u00e9ter 100 t\u00e2ches \u2014 ma\u00eetre de la for\u00eat',
        'category_id': cat_teamwork,
        'xp_reward': 150,
        'rarity': 'epic',
        'condition_type': 'automatic',
        'condition_model': 'project.task',
        'condition_domain': "[('stage_id.fold', '=', True)]",
        'condition_threshold': 100,
        'condition_user_field': 'user_ids',
        'popup_effect': 'fireworks',
        'popup_message': 'La for\u00eat enti\u00e8re conna\u00eet votre nom !',
        'hidden': True,
    })

    # ── Helpdesk: 10 tickets ──
    _get_or_create('badge_10_tickets', {
        'name': 'D\u00e9panneur Aguerri',
        'description': 'R\u00e9soudre 10 tickets \u2014 on vous fait confiance',
        'category_id': cat_teamwork,
        'xp_reward': 25,
        'rarity': 'uncommon',
        'condition_type': 'automatic',
        'condition_model': 'helpdesk.ticket',
        'condition_domain': "[('stage_id.fold', '=', True)]",
        'condition_threshold': 10,
        'condition_user_field': 'user_id',
        'popup_effect': 'confetti',
        'popup_message': 'La meute peut compter sur vous !',
    })

    # ── Hosting: 10 + 50 maintenance ──
    _get_or_create('badge_10_maintenance', {
        'name': 'Technicien de la Tani\u00e8re',
        'description': 'Compl\u00e9ter 10 maintenances \u2014 les murs tiennent bon',
        'category_id': cat_hosting,
        'xp_reward': 25,
        'rarity': 'uncommon',
        'condition_type': 'automatic',
        'condition_model': 'hosting.maintenance.schedule',
        'condition_domain': "[('last_performed_date', '!=', False)]",
        'condition_threshold': 10,
        'condition_user_field': 'write_uid',
        'popup_effect': 'confetti',
        'popup_message': 'Les fondations sont solides !',
    })
    _get_or_create('badge_50_maintenance', {
        'name': 'Ma\u00eetre de la Tani\u00e8re',
        'description': 'Compl\u00e9ter 50 maintenances \u2014 architecte de la tani\u00e8re',
        'category_id': cat_hosting,
        'xp_reward': 75,
        'rarity': 'rare',
        'condition_type': 'automatic',
        'condition_model': 'hosting.maintenance.schedule',
        'condition_domain': "[('last_performed_date', '!=', False)]",
        'condition_threshold': 50,
        'condition_user_field': 'write_uid',
        'popup_effect': 'fireworks',
        'popup_message': 'La tani\u00e8re est une forteresse !',
    })

    # ── Knowledge: 25 + 50 documents ──
    _get_or_create('badge_25_docs', {
        'name': 'Archiviste',
        'description': 'Cr\u00e9er 25 documents \u2014 la m\u00e9moire du terrier',
        'category_id': cat_knowledge,
        'xp_reward': 30,
        'rarity': 'uncommon',
        'condition_type': 'automatic',
        'condition_model': 'project.document',
        'condition_domain': '[]',
        'condition_threshold': 25,
        'popup_effect': 'confetti',
        'popup_message': "Rien n'\u00e9chappe \u00e0 vos archives !",
    })
    _get_or_create('badge_50_docs', {
        'name': 'Biblioth\u00e9caire',
        'description': 'Cr\u00e9er 50 documents \u2014 gardien de tout le savoir',
        'category_id': cat_knowledge,
        'xp_reward': 100,
        'rarity': 'epic',
        'condition_type': 'automatic',
        'condition_model': 'project.document',
        'condition_domain': '[]',
        'condition_threshold': 50,
        'popup_effect': 'fireworks',
        'popup_message': 'Le terrier d\u00e9borde de sagesse !',
        'hidden': True,
    })

    # ── Communication: 100 messages ──
    _get_or_create('badge_100_messages', {
        'name': 'Voix de la For\u00eat',
        'description': 'Poster 100 messages \u2014 votre voix r\u00e9sonne partout',
        'category_id': cat_communication,
        'xp_reward': 50,
        'rarity': 'rare',
        'condition_type': 'automatic',
        'condition_model': 'mail.message',
        'condition_domain': "[('message_type', '=', 'comment')]",
        'condition_threshold': 100,
        'popup_effect': 'fireworks',
        'popup_message': 'Chaque renard conna\u00eet votre voix !',
    })

    # ── Streaks: 90-day ──
    _get_or_create('badge_streak_90', {
        'name': 'Flamme \u00c9ternelle',
        'description': 'Maintenir votre flamme pendant 90 jours cons\u00e9cutifs',
        'category_id': cat_productivity,
        'xp_reward': 200,
        'rarity': 'legendary',
        'condition_type': 'threshold',
        'threshold_field': 'longest_streak',
        'condition_threshold': 90,
        'popup_effect': 'fireworks',
        'popup_message': 'Votre flamme illumine toute la for\u00eat !',
        'hidden': True,
    })

    # ── Milestones: 1000, 2500, 10000, 20000 XP ──
    _get_or_create('badge_xp_1000', {
        'name': 'Renard de Platine',
        'description': 'Atteindre 1 000 XP',
        'category_id': cat_milestone,
        'xp_reward': 50,
        'rarity': 'rare',
        'condition_type': 'threshold',
        'condition_threshold': 1000,
        'popup_effect': 'confetti',
        'popup_message': 'Le platine vous va \u00e0 merveille !',
    })
    _get_or_create('badge_xp_2500', {
        'name': 'Renard de Diamant',
        'description': 'Atteindre 2 500 XP',
        'category_id': cat_milestone,
        'xp_reward': 75,
        'rarity': 'epic',
        'condition_type': 'threshold',
        'condition_threshold': 2500,
        'popup_effect': 'fireworks',
        'popup_message': 'Dur comme le diamant !',
    })
    _get_or_create('badge_xp_10000', {
        'name': 'Renard \u00c9ternel',
        'description': 'Atteindre 10 000 XP \u2014 une l\u00e9gende vivante',
        'category_id': cat_milestone,
        'xp_reward': 200,
        'rarity': 'legendary',
        'condition_type': 'threshold',
        'condition_threshold': 10000,
        'popup_effect': 'fireworks',
        'popup_message': 'Votre l\u00e9gende traversera les \u00e2ges !',
        'hidden': True,
    })
    _get_or_create('badge_xp_20000', {
        'name': 'Kitsune L\u00e9gendaire',
        'description': 'Atteindre 20 000 XP \u2014 transcendance renarde',
        'category_id': cat_milestone,
        'xp_reward': 500,
        'rarity': 'legendary',
        'condition_type': 'threshold',
        'condition_threshold': 20000,
        'popup_effect': 'fireworks',
        'popup_message': 'Vous \u00eates devenu Kitsune !',
        'hidden': True,
    })
