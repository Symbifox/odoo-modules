"""Fox Quest v2.0 — Fox theming + new XP sources (message, helpdesk, activity)."""
import logging

from odoo import api, SUPERUSER_ID

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})

    _update_levels(env)
    _update_badge_categories(env)
    _update_badges(env)
    _create_communication_category(env)
    _create_new_xp_rules(env)
    _create_new_badges(env)

    # Retroactive XP for last 96 hours
    env['bf.gamification.profile']._backfill_recent_xp(hours=96)


def _update_levels(env):
    """Rename levels to fox theme."""
    updates = {
        'bf_gamification.level_bronze': {'name': 'Renardeau', 'title': 'Louveteau'},
        'bf_gamification.level_bronze_2': {'name': 'Jeune Renard', 'title': '\u00c9claireur'},
        'bf_gamification.level_argent': {'name': 'Renard Roux', 'title': 'Pisteur'},
        'bf_gamification.level_argent_2': {'name': 'Renard Argent\u00e9', 'title': 'Chasseur'},
        'bf_gamification.level_or': {'name': 'Renard Arctique', 'title': 'Gardien'},
        'bf_gamification.level_or_2': {'name': 'Renard Dor\u00e9', 'title': 'Alpha'},
        'bf_gamification.level_platine': {'name': 'Renard Mystique', 'title': 'Sage'},
        'bf_gamification.level_platine_2': {'name': 'Esprit Renard', 'title': 'Oracle'},
        'bf_gamification.level_diamant': {'name': 'Renard C\u00e9leste', 'title': 'L\u00e9gende'},
        'bf_gamification.level_diamant_2': {'name': 'Kitsune', 'title': 'Kitsune'},
    }
    for xmlid, vals in updates.items():
        record = env.ref(xmlid, raise_if_not_found=False)
        if record:
            record.write(vals)
            _logger.info("Fox Quest: updated level %s → %s", xmlid, vals['name'])


def _update_badge_categories(env):
    """Rename badge categories to fox theme."""
    updates = {
        'bf_gamification.badge_cat_productivity': {'name': 'La Chasse'},
        'bf_gamification.badge_cat_knowledge': {'name': 'Le Terrier'},
        'bf_gamification.badge_cat_teamwork': {'name': 'La Meute'},
        'bf_gamification.badge_cat_hosting': {'name': 'La Tani\u00e8re'},
        'bf_gamification.badge_cat_milestone': {'name': 'Les \u00c9toiles'},
    }
    for xmlid, vals in updates.items():
        record = env.ref(xmlid, raise_if_not_found=False)
        if record:
            record.write(vals)
            _logger.info("Fox Quest: updated category %s → %s", xmlid, vals['name'])


def _update_badges(env):
    """Rename existing badges to fox theme."""
    updates = {
        'bf_gamification.badge_first_timesheet': {
            'name': 'Premi\u00e8res Pattes',
            'description': 'Poser vos premi\u00e8res pattes dans Fox Quest',
            'popup_message': 'Bienvenue dans la meute !',
        },
        'bf_gamification.badge_100_hours': {
            'name': 'Renard Endurant',
            'description': 'Accumuler 100 heures de feuilles de temps',
        },
        'bf_gamification.badge_streak_7': {
            'name': 'Queue de Feu',
            'description': 'Maintenir votre flamme pendant 7 jours cons\u00e9cutifs',
            'popup_message': 'Votre queue brille dans la nuit !',
        },
        'bf_gamification.badge_streak_30': {
            'name': 'Renard Infatigable',
            'description': 'Maintenir votre flamme pendant 30 jours cons\u00e9cutifs',
            'popup_message': 'Rien ne peut \u00e9teindre votre flamme !',
        },
        'bf_gamification.badge_first_doc': {
            'name': 'Renard Lettr\u00e9',
            'description': 'Cr\u00e9er votre premier document',
            'popup_message': 'La plume est plus forte que la griffe !',
        },
        'bf_gamification.badge_10_docs': {
            'name': 'Sage du Terrier',
            'description': 'Cr\u00e9er 10 documents dans le terrier',
            'popup_message': 'Gardien du savoir ancestral !',
        },
        'bf_gamification.badge_first_task': {
            'name': 'Renard Aventurier',
            'description': 'Compl\u00e9ter votre premi\u00e8re t\u00e2che',
            'popup_message': 'La for\u00eat n\'attend que vous !',
        },
        'bf_gamification.badge_50_tasks': {
            'name': 'Renard Implacable',
            'description': 'Compl\u00e9ter 50 t\u00e2ches comme un vrai renard',
            'popup_message': 'Aucune proie ne vous \u00e9chappe !',
        },
        'bf_gamification.badge_first_maintenance': {
            'name': 'Gardien de la Tani\u00e8re',
            'description': 'Compl\u00e9ter votre premi\u00e8re maintenance',
            'popup_message': 'La tani\u00e8re est entre bonnes pattes !',
        },
        'bf_gamification.badge_xp_500': {
            'name': 'Renard d\'Argent',
            'description': 'Atteindre 500 XP',
            'popup_message': 'Votre pelage brille d\'argent !',
        },
        'bf_gamification.badge_xp_5000': {
            'name': 'Renard d\'Or',
            'description': 'Atteindre 5 000 XP',
            'popup_message': 'Votre pelage brille d\'or pur !',
        },
    }
    for xmlid, vals in updates.items():
        record = env.ref(xmlid, raise_if_not_found=False)
        if record:
            record.write(vals)
            _logger.info("Fox Quest: updated badge %s → %s", xmlid, vals['name'])


def _create_communication_category(env):
    """Create the new 'Le Glapissement' badge category."""
    xmlid = 'bf_gamification.badge_cat_communication'
    existing = env.ref(xmlid, raise_if_not_found=False)
    if existing:
        return existing
    cat = env['bf.gamification.badge.category'].create({
        'name': 'Le Glapissement',
        'sequence': 6,
        'color': 2,
        'description': 'Badges li\u00e9s \u00e0 la communication et aux messages',
    })
    env['ir.model.data'].create({
        'module': 'bf_gamification',
        'name': 'badge_cat_communication',
        'model': 'bf.gamification.badge.category',
        'res_id': cat.id,
        'noupdate': True,
    })
    _logger.info("Fox Quest: created badge category 'Le Glapissement'")
    return cat


def _ensure_record(env, xmlid, model, vals):
    """Get or create a record with an xmlid."""
    record = env.ref(xmlid, raise_if_not_found=False)
    if record:
        return record
    record = env[model].create(vals)
    module, name = xmlid.split('.', 1)
    env['ir.model.data'].create({
        'module': module,
        'name': name,
        'model': model,
        'res_id': record.id,
        'noupdate': True,
    })
    _logger.info("Fox Quest: created %s '%s'", model, vals.get('name', ''))
    return record


def _create_new_xp_rules(env):
    """Create XP rules for message, helpdesk, and activity sources."""
    _ensure_record(env, 'bf_gamification.xp_rule_message', 'bf.gamification.xp.rule', {
        'name': 'Message post\u00e9',
        'source': 'message',
        'trigger': 'create',
        'xp_amount': 1,
        'condition_description': 'Par message ou note interne',
    })
    _ensure_record(env, 'bf_gamification.xp_rule_ticket_resolve', 'bf.gamification.xp.rule', {
        'name': 'Ticket r\u00e9solu',
        'source': 'helpdesk',
        'trigger': 'complete',
        'xp_amount': 3,
        'condition_description': 'Ticket pass\u00e9 en \u00ab R\u00e9solu \u00bb',
    })
    _ensure_record(env, 'bf_gamification.xp_rule_activity_done', 'bf.gamification.xp.rule', {
        'name': 'Activit\u00e9 compl\u00e9t\u00e9e',
        'source': 'activity',
        'trigger': 'complete',
        'xp_amount': 1,
        'condition_description': 'Activit\u00e9 planifi\u00e9e termin\u00e9e',
    })


def _create_new_badges(env):
    """Create new badges for communication and helpdesk."""
    comm_cat = env.ref('bf_gamification.badge_cat_communication', raise_if_not_found=False)
    team_cat = env.ref('bf_gamification.badge_cat_teamwork', raise_if_not_found=False)

    if comm_cat:
        _ensure_record(env, 'bf_gamification.badge_first_message', 'bf.gamification.badge', {
            'name': 'Premier Glapissement',
            'description': 'Poster votre premier message',
            'category_id': comm_cat.id,
            'xp_reward': 10,
            'rarity': 'common',
            'condition_type': 'automatic',
            'condition_model': 'mail.message',
            'condition_domain': "[('message_type', '=', 'comment')]",
            'condition_threshold': 1,
            'popup_effect': 'confetti',
            'popup_message': 'Votre voix porte dans la for\u00eat !',
        })
        _ensure_record(env, 'bf_gamification.badge_50_messages', 'bf.gamification.badge', {
            'name': 'Renard Bavard',
            'description': 'Poster 50 messages \u2014 la for\u00eat enti\u00e8re vous entend',
            'category_id': comm_cat.id,
            'xp_reward': 25,
            'rarity': 'uncommon',
            'condition_type': 'automatic',
            'condition_model': 'mail.message',
            'condition_domain': "[('message_type', '=', 'comment')]",
            'condition_threshold': 50,
            'popup_effect': 'confetti',
            'popup_message': 'Le renard le plus bavard de la for\u00eat !',
        })

    if team_cat:
        _ensure_record(env, 'bf_gamification.badge_first_ticket', 'bf.gamification.badge', {
            'name': 'D\u00e9panneur',
            'description': 'R\u00e9soudre votre premier ticket d\'assistance',
            'category_id': team_cat.id,
            'xp_reward': 10,
            'rarity': 'common',
            'condition_type': 'manual',
            'popup_effect': 'confetti',
            'popup_message': 'Toujours pr\u00eat \u00e0 aider la meute !',
        })
        _ensure_record(env, 'bf_gamification.badge_25_tickets', 'bf.gamification.badge', {
            'name': 'Renard Serviable',
            'description': 'R\u00e9soudre 25 tickets \u2014 toujours pr\u00eat \u00e0 d\u00e9panner',
            'category_id': team_cat.id,
            'xp_reward': 50,
            'rarity': 'rare',
            'condition_type': 'manual',
            'popup_effect': 'fireworks',
            'popup_message': 'Le h\u00e9ros de la meute !',
        })
