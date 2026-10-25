from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError

from . import fq_days, fq_savepoint
from .gamification_constance import CAUGHT_UP_KINDS

CAUGHT_UP_LISTS = {
    'activity': ("Activités en retard ou du jour", 'fa-tasks'),
    'inbox': ("Courriels à traiter", 'fa-inbox'),
    'task': ("Tâches en retard", 'fa-check-square-o'),
    'report': ("Comptes rendus à envoyer", 'fa-file-text-o'),
    'agenda': ("Ordres du jour des 48 h à envoyer", 'fa-list-ol'),
}
CLAIM_STATES = {'pending': "en attente", 'approved': "approuvée",
                'refused': "refusée", 'consumed': "prise"}


def _days_label(days):
    text = ('%g' % days).replace('.', ',')
    return '%s jour%s' % (text, 's' if days > 1 else '')


class GamificationDashboard(models.AbstractModel):
    _name = 'bf.gamification.dashboard'
    _description = 'Tableau de bord Fox Quest'
    # `AbstractModel` : ce modèle n'a ni champ ni table, il ne sert que de point
    # d'entrée RPC pour le composant OWL. Déclaré `models.Model` + `_auto = False`,
    # il entrait dans `Registry.check_tables_exist()`, qui ne dispense que
    # `_abstract` et les modèles à `_table_query` — d'où un `ERROR
    # odoo.modules.registry: Model <ce modèle> has no table.` journalisé à chaque
    # passe du chargeur sur une base neuve.


    @staticmethod
    def _avatar_uri(user):
        """L'adresse de l'avatar, par la route d'image d'Odoo.

        🔴 Le gabarit envoyait `data:image/png;base64,` + le champ image. Or
        l'avatar par defaut d'Odoo n'est pas un PNG : c'est un SVG genere
        (initiales sur fond colore). Le navigateur refusait donc de le decoder,
        et l'image paraissait cassee pour toute personne sans photo televersee,
        c'est-a-dire presque tout le monde. Releve le 2026-09-19 en capturant
        une base de demonstration.

        ⚠️ Poser le bon type de contenu dans le data URI ne suffisait pas non
        plus. On passe donc par `/web/image`, qui sert l'octet exact avec son
        type, se met en cache cote navigateur, et retire au passage plusieurs
        kilo-octets de base64 de chaque ligne du classement.
        """
        return "/web/image/res.users/%d/avatar_128" % user.id if user else False

    @api.model
    def get_dashboard_data(self):
        """Return all dashboard data for the OWL component."""
        user = self.env.user
        Profile = self.env['bf.gamification.profile']
        profile = Profile._get_or_create_profile(user)

        achievements = self._get_achievements_data(user)
        # Les défis du jour ne cassent jamais le tableau de bord : la barre du haut
        # l'appelle à chaque page.
        challenges = {'enabled': False, 'lines': [], 'stamps': 0, 'per_card': 0,
                      'cards_completed': 0, 'rest_day': False, 'all_done': False}
        with fq_savepoint.isolated(self.env, "défis du jour (tableau de bord)"):
            challenges = Profile._bf_challenges_data(user)
        return {
            'profile': self._get_profile_data(profile),
            'leaderboard': self._get_leaderboard(),
            'recent_badges': self._get_recent_badges(user),
            'xp_history': self._get_xp_history(user),
            'stats': self._get_global_stats(),
            'available_rewards': self._get_available_rewards(),
            'achievements': achievements,
            'next_goals': self._get_next_goals(achievements),
            'challenges': challenges,
            'constance': self._get_constance_data(profile),
        }

    @api.model
    def set_worn_title(self, badge_id):
        """Porter, ou retirer (`False`), le nom d'un badge obtenu comme titre.

        Toujours le profil de qui appelle, et jamais sous usurpation du support :
        le titre est un trait de la personne.
        """
        Profile = self.env['bf.gamification.profile']
        Profile._bf_refuse_if_impersonated(
            _("Le support ne change pas le titre d'une personne."))
        if not self.env.user._is_internal():
            raise AccessError(_("Les titres sont réservés aux usagers internes."))
        if badge_id is None or badge_id is False:
            badge_id = False
        elif type(badge_id) is not int or not 0 < badge_id < 2 ** 31:
            raise AccessError(_("Badge invalide."))
        profile = Profile._get_or_create_profile(self.env.user)
        profile._bf_set_worn_title(badge_id)
        return self._get_worn_title(profile)

    @api.model
    def _get_worn_title(self, profile):
        badge = profile.sudo().worn_badge_id
        if not badge:
            return False
        return {'badge_id': badge.id, 'name': badge.name, 'rarity': badge.rarity}

    @api.model
    def _get_next_goals(self, achievements, limit=3):
        """Les prochains objectifs : les badges à compteur les plus avancés.

        Un badge caché reste une surprise, un badge manuel n'a pas de compteur.
        """
        goals = []
        for category in achievements['categories']:
            for badge in category['badges']:
                progress = badge['progress'] or {}
                if (badge['earned'] or badge['hidden'] or badge['kind'] == 'manual'
                        or not progress.get('target') or progress.get('percent', 0) >= 100):
                    continue
                goals.append({
                    'id': badge['id'],
                    'name': badge['name'],
                    'description': badge['description'],
                    'rarity': badge['rarity'],
                    'xp_reward': badge['xp_reward'],
                    'current': progress['current'],
                    'target': progress['target'],
                    'percent': progress['percent'],
                })
        goals.sort(key=lambda goal: (-goal['percent'], goal['target'], goal['id']))
        return goals[:limit]

    @api.model
    def set_leave_opt_in(self, flag):
        """Participer, ou ne plus participer, aux congés de constance.

        Toujours le profil de qui appelle : la participation ne se pose pour
        personne d'autre.
        """
        if not self.env.user._is_internal():
            raise AccessError(_("Les congés de constance sont réservés aux usagers internes."))
        profile = self.env['bf.gamification.profile']._get_or_create_profile(self.env.user)
        profile._bf_set_leave_opt_in(bool(flag))
        return self._get_constance_data(profile)

    @api.model
    def _get_constance_data(self, profile):
        Profile = self.env['bf.gamification.profile']
        user = profile.user_id
        lists = []
        for kind in CAUGHT_UP_KINDS:
            label, icon = CAUGHT_UP_LISTS[kind]
            lists.append({'kind': kind, 'label': label, 'icon': icon,
                          'pending': Profile._bf_pending_count(user, kind)})
        settings = Profile._bf_leave_settings()
        leave = {'enabled': settings['enabled']}
        if settings['enabled']:
            sprofile = profile.sudo()
            reward = self.env.ref('bf_gamification.reward_leave_constance',
                                  raise_if_not_found=False)
            claims = self.env['bf.gamification.reward.claim'].sudo().search([
                ('user_id', '=', user.id), ('reward_id', '=', reward.id if reward else 0),
            ], order='date_claimed desc', limit=5)
            step = settings['step'] or 1
            leave.update({
                'opt_in': sprofile.leave_opt_in,
                'can_join': Profile._bf_is_player(user),
                'progress': sprofile.leave_progress,
                'step': settings['step'],
                'percent': min(round(100.0 * sprofile.leave_progress / step, 1), 100),
                'days_label': _days_label(settings['days_per_step']),
                'max_per_year': settings['max_per_year'],
                'claims': [{
                    'id': c.id,
                    'date': str(fq_days.local_date(user, c.date_claimed)),
                    'days_label': _days_label(c.leave_days),
                    'state': c.state,
                    'state_label': CLAIM_STATES.get(c.state, c.state),
                } for c in claims],
            })
        return {
            'lists': lists,
            'all_clear': not any(item['pending'] for item in lists),
            'leave': leave,
        }

    @api.model
    def toggle_showcase_badge(self, user_badge_id):
        """Pin or unpin a badge from the user's showcase. Returns updated showcase list."""
        # Comme le titre porté : un trait de la personne, que le support ne change pas.
        self.env['bf.gamification.profile']._bf_refuse_if_impersonated(
            _("Le support ne change pas la vitrine d'une personne."))
        user = self.env.user
        profile = self.env['bf.gamification.profile']._get_or_create_profile(user)
        user_badge = self.env['bf.gamification.user.badge'].browse(user_badge_id)
        if not user_badge.exists() or user_badge.user_id.id != user.id:
            return {'error': 'Badge non trouv\u00e9'}

        if user_badge in profile.showcase_badge_ids:
            profile.write({'showcase_badge_ids': [(3, user_badge.id)]})
        else:
            if len(profile.showcase_badge_ids) >= 5:
                return {'error': 'Maximum 5 badges dans la vitrine'}
            profile.write({'showcase_badge_ids': [(4, user_badge.id)]})

        return {'showcase_ids': profile.showcase_badge_ids.ids}

    @api.model
    def _get_profile_data(self, profile):
        showcase = []
        for ub in profile.showcase_badge_ids:
            showcase.append({
                'user_badge_id': ub.id,
                'name': ub.badge_id.name,
                'rarity': ub.badge_id.rarity,
                'image': ub.badge_id.image,
            })
        return {
            'id': profile.id,
            'user_name': profile.user_id.name,
            'avatar': self._avatar_uri(profile.user_id),
            'total_xp': profile.total_xp,
            'xp_balance': profile.xp_balance,
            'level_name': profile.level_id.name if profile.level_id else '',
            'level_title': profile.title or '',
            'level_css_class': profile.level_id.css_class if profile.level_id else '',
            'xp_to_next_level': profile.xp_to_next_level,
            'progress_percent': round(profile.progress_percent, 1),
            'current_streak': profile.current_streak,
            'longest_streak': profile.longest_streak,
            'badge_count': profile.badge_count,
            'rank': profile.rank,
            'xp_this_week': profile.xp_this_week,
            'xp_this_month': profile.xp_this_month,
            'showcase_badges': showcase,
            'worn_title': self._get_worn_title(profile),
        }

    @api.model
    def _get_leaderboard(self, limit=10):
        Profile = self.env['bf.gamification.profile']
        profiles = Profile.search(Profile._bf_player_domain(), order='total_xp desc', limit=limit)
        result = []
        for idx, p in enumerate(profiles):
            result.append({
                'rank': idx + 1,
                'user_name': p.user_id.name,
                'avatar': self._avatar_uri(p.user_id),
                'total_xp': p.total_xp,
                'level_name': p.level_id.name if p.level_id else '',
                'level_title': p.title or '',
                'level_css_class': p.level_id.css_class if p.level_id else '',
                'badge_count': p.badge_count,
                'current_streak': p.current_streak,
                'worn_title': p.worn_badge_id.name or '',
            })
        return result

    @api.model
    def _get_recent_badges(self, user, limit=6):
        user_badges = self.env['bf.gamification.user.badge'].search([
            ('user_id', '=', user.id),
        ], order='date_earned desc', limit=limit)
        return [{
            'name': ub.badge_id.name,
            'description': ub.badge_id.description or '',
            'rarity': ub.badge_id.rarity,
            'date_earned': str(ub.date_earned),
            'xp_reward': ub.badge_id.xp_reward,
        } for ub in user_badges]

    @api.model
    def _get_xp_history(self, user, days=30):
        since = fields.Datetime.now() - timedelta(days=days)
        txns = self.env['bf.gamification.xp.transaction'].search([
            ('user_id', '=', user.id),
            ('date', '>=', since),
        ], order='date desc', limit=50)
        return [{
            'xp_amount': t.xp_amount,
            'source': t.source,
            'description': t.description or '',
            'date': str(t.date),
        } for t in txns]

    @api.model
    def _get_global_stats(self):
        Profile = self.env['bf.gamification.profile']
        players = Profile._bf_player_domain()
        total_profiles = Profile.search_count(players)
        total_xp = sum(Profile.search(players).mapped('total_xp'))
        total_badges = self.env['bf.gamification.user.badge'].search_count([])
        return {
            'total_players': total_profiles,
            'total_xp_awarded': total_xp,
            'total_badges_earned': total_badges,
        }

    @api.model
    def _get_available_rewards(self, limit=6):
        rewards = self.env['bf.gamification.reward'].search([
            ('available', '=', True),
            ('active', '=', True),
        ], order='xp_cost asc', limit=limit)
        return [{
            'id': r.id,
            'name': r.name,
            'xp_cost': r.xp_cost,
            'category': r.category,
            'stock': r.stock,
        } for r in rewards]

    @api.model
    def _get_achievements_data(self, user):
        """Return all badges with state, progress, rarity %, grouped by category."""
        Badge = self.env['bf.gamification.badge']
        UserBadge = self.env['bf.gamification.user.badge']
        Profile = self.env['bf.gamification.profile']
        profile = Profile._get_or_create_profile(user)

        all_badges = Badge.search([('active', '=', True)], order='category_id, rarity, name')

        # Earned badge IDs for this user
        earned_ubs = UserBadge.search([('user_id', '=', user.id)])
        earned_badge_ids = set(earned_ubs.mapped('badge_id').ids)
        # Map badge_id -> user_badge record (first one)
        ub_map = {}
        for ub in earned_ubs:
            if ub.badge_id.id not in ub_map:
                ub_map[ub.badge_id.id] = ub

        showcase_ub_ids = set(profile.showcase_badge_ids.ids)

        categories = {}
        for badge in all_badges:
            cat_name = badge.category_id.name if badge.category_id else "Autre"
            cat_id = badge.category_id.id if badge.category_id else 0
            if cat_id not in categories:
                categories[cat_id] = {
                    'name': cat_name,
                    'badges': [],
                    'earned_count': 0,
                    'total_count': 0,
                }

            earned = badge.id in earned_badge_ids
            progress = badge._get_user_progress(user, earned_ids=earned_badge_ids, profile=profile)
            ub = ub_map.get(badge.id)

            badge_data = {
                'id': badge.id,
                'user_badge_id': ub.id if ub else False,
                'name': badge.name if (earned or not badge.hidden) else '???',
                'description': (badge.description or '') if (earned or not badge.hidden) else '',
                'image': badge.image if earned else (badge.image_locked or False),
                'rarity': badge.rarity,
                'kind': badge.condition_type,
                'xp_reward': badge.xp_reward,
                'earned': earned,
                'hidden': badge.hidden and not earned,
                'earned_percentage': badge.earned_percentage,
                'progress': progress,
                'showcased': ub.id in showcase_ub_ids if ub else False,
                'date_earned': str(ub.date_earned) if ub else False,
            }
            categories[cat_id]['badges'].append(badge_data)
            categories[cat_id]['total_count'] += 1
            if earned:
                categories[cat_id]['earned_count'] += 1

        # Convert to sorted list
        result = sorted(categories.values(), key=lambda c: c['name'])

        # Overall stats
        total = sum(c['total_count'] for c in result)
        earned_total = sum(c['earned_count'] for c in result)

        return {
            'categories': result,
            'total_badges': total,
            'earned_badges': earned_total,
            'completion_percent': round(earned_total / total * 100, 1) if total else 0,
        }
