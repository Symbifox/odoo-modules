from datetime import timedelta

from odoo import api, fields, models


class GamificationDashboard(models.AbstractModel):
    _name = 'bf.gamification.dashboard'
    _description = 'Tableau de bord Fox Quest'
    # `AbstractModel` : ce modèle n'a ni champ ni table, il ne sert que de point
    # d'entrée RPC pour le composant OWL. Déclaré `models.Model` + `_auto = False`,
    # il entrait dans `Registry.check_tables_exist()`, qui ne dispense que
    # `_abstract` et les modèles à `_table_query` — d'où un `ERROR
    # odoo.modules.registry: Model <ce modèle> has no table.` journalisé à chaque
    # passe du chargeur sur une base neuve (BF #24867).

    @api.model
    def get_dashboard_data(self):
        """Return all dashboard data for the OWL component."""
        user = self.env.user
        Profile = self.env['bf.gamification.profile']
        profile = Profile._get_or_create_profile(user)

        return {
            'profile': self._get_profile_data(profile),
            'leaderboard': self._get_leaderboard(),
            'recent_badges': self._get_recent_badges(user),
            'xp_history': self._get_xp_history(user),
            'stats': self._get_global_stats(),
            'available_rewards': self._get_available_rewards(),
            'achievements': self._get_achievements_data(user),
        }

    @api.model
    def toggle_showcase_badge(self, user_badge_id):
        """Pin or unpin a badge from the user's showcase. Returns updated showcase list."""
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
            'avatar': profile.avatar or profile.user_id.avatar_128,
            'total_xp': profile.total_xp,
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
        }

    @api.model
    def _get_leaderboard(self, limit=10):
        profiles = self.env['bf.gamification.profile'].search(
            [], order='total_xp desc', limit=limit)
        result = []
        for idx, p in enumerate(profiles):
            result.append({
                'rank': idx + 1,
                'user_name': p.user_id.name,
                'avatar': p.user_id.avatar_128,
                'total_xp': p.total_xp,
                'level_name': p.level_id.name if p.level_id else '',
                'level_title': p.title or '',
                'level_css_class': p.level_id.css_class if p.level_id else '',
                'badge_count': p.badge_count,
                'current_streak': p.current_streak,
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
        total_profiles = Profile.search_count([])
        total_xp = sum(Profile.search([]).mapped('total_xp'))
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
            progress = badge._get_user_progress(user)
            ub = ub_map.get(badge.id)

            badge_data = {
                'id': badge.id,
                'user_badge_id': ub.id if ub else False,
                'name': badge.name if (earned or not badge.hidden) else '???',
                'description': (badge.description or '') if (earned or not badge.hidden) else '',
                'image': badge.image if earned else (badge.image_locked or False),
                'rarity': badge.rarity,
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
