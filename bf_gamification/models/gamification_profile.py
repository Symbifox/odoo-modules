import logging
from datetime import datetime, timedelta

from odoo import api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools.safe_eval import safe_eval

from .gamification_badge import ALLOWED_CONDITION_MODELS

_logger = logging.getLogger(__name__)


class GamificationProfile(models.Model):
    _name = 'bf.gamification.profile'
    _description = 'Profil Fox Quest'
    _order = 'total_xp desc'

    user_id = fields.Many2one('res.users', string="Utilisateur", required=True,
                              ondelete='cascade', index=True)
    avatar = fields.Image(string="Avatar", max_width=256, max_height=256)
    total_xp = fields.Integer(string="XP total", compute='_compute_xp', store=True)
    level_id = fields.Many2one('bf.gamification.level', string="Niveau",
                               compute='_compute_level', store=True)
    xp_to_next_level = fields.Integer(string="XP avant prochain niveau",
                                       compute='_compute_level', store=True)
    progress_percent = fields.Float(string="Progression (%)",
                                     compute='_compute_level', store=True)
    current_streak = fields.Integer(string="Streak actuel", default=0)
    longest_streak = fields.Integer(string="Meilleur streak", default=0)
    last_activity_date = fields.Date(string="Dernière activité")
    badge_ids = fields.Many2many('bf.gamification.user.badge', string="Badges obtenus",
                                  compute='_compute_badges')
    badge_count = fields.Integer(string="Nombre de badges", compute='_compute_badges')
    showcase_badge_ids = fields.Many2many(
        'bf.gamification.user.badge',
        'bf_gamification_profile_showcase_rel',
        'profile_id', 'user_badge_id',
        string="Vitrine",
        help="Badges \u00e9pingl\u00e9s sur le profil (max 5)",
    )
    rank = fields.Integer(string="Classement", compute='_compute_rank')
    title = fields.Char(string="Titre", compute='_compute_level', store=True)
    xp_this_week = fields.Integer(string="XP cette semaine", compute='_compute_xp_periods')
    xp_this_month = fields.Integer(string="XP ce mois", compute='_compute_xp_periods')

    _sql_constraints = [
        ('user_unique', 'UNIQUE(user_id)', 'Un seul profil par utilisateur.'),
    ]

    @api.constrains('showcase_badge_ids')
    def _check_showcase_limit(self):
        for profile in self:
            if len(profile.showcase_badge_ids) > 5:
                raise ValidationError("La vitrine ne peut contenir que 5 badges maximum.")

    @api.depends('user_id')
    def _compute_xp(self):
        for profile in self:
            total = sum(
                self.env['bf.gamification.xp.transaction'].search([
                    ('user_id', '=', profile.user_id.id),
                ]).mapped('xp_amount')
            )
            profile.total_xp = max(total, 0)

    @api.depends('total_xp')
    def _compute_level(self):
        levels = self.env['bf.gamification.level'].search([], order='min_xp asc')
        for profile in self:
            current_level = self.env['bf.gamification.level']
            next_level = self.env['bf.gamification.level']
            for i, level in enumerate(levels):
                if profile.total_xp >= level.min_xp:
                    current_level = level
                    next_level = levels[i + 1] if i + 1 < len(levels) else self.env['bf.gamification.level']
            profile.level_id = current_level
            profile.title = current_level.title if current_level else ''
            if next_level:
                xp_in_level = profile.total_xp - current_level.min_xp
                xp_range = next_level.min_xp - current_level.min_xp
                profile.xp_to_next_level = next_level.min_xp - profile.total_xp
                profile.progress_percent = (xp_in_level / xp_range * 100) if xp_range > 0 else 100
            else:
                profile.xp_to_next_level = 0
                profile.progress_percent = 100

    def _compute_badges(self):
        UserBadge = self.env['bf.gamification.user.badge']
        for profile in self:
            badges = UserBadge.search([('user_id', '=', profile.user_id.id)])
            profile.badge_ids = badges
            profile.badge_count = len(badges)

    def _compute_rank(self):
        all_profiles = self.search([], order='total_xp desc')
        rank_map = {p.id: idx + 1 for idx, p in enumerate(all_profiles)}
        for profile in self:
            profile.rank = rank_map.get(profile.id, 0)

    def _compute_xp_periods(self):
        today = fields.Date.today()
        week_start = today - timedelta(days=today.weekday())
        month_start = today.replace(day=1)
        for profile in self:
            week_txns = self.env['bf.gamification.xp.transaction'].search([
                ('user_id', '=', profile.user_id.id),
                ('date', '>=', fields.Datetime.to_datetime(week_start)),
            ])
            profile.xp_this_week = sum(week_txns.mapped('xp_amount'))
            month_txns = self.env['bf.gamification.xp.transaction'].search([
                ('user_id', '=', profile.user_id.id),
                ('date', '>=', fields.Datetime.to_datetime(month_start)),
            ])
            profile.xp_this_month = sum(month_txns.mapped('xp_amount'))

    def _get_or_create_profile(self, user):
        """Get or create a gamification profile for the given user."""
        profile = self.search([('user_id', '=', user.id)], limit=1)
        if not profile:
            profile = self.sudo().create({'user_id': user.id})
        return profile

    def _award_xp(self, amount, source, description, reference=None):
        """Central method to award XP. Creates transaction, recalculates level,
        checks badges, and triggers popups if applicable."""
        self.ensure_one()
        # Check if gamification is enabled
        if not self.env['ir.config_parameter'].sudo().get_param(
                'bf_gamification.gamification_enabled', 'True') == 'True':
            return

        old_level = self.level_id

        # Create XP transaction
        vals = {
            'user_id': self.user_id.id,
            'xp_amount': amount,
            'source': source,
            'description': description,
        }
        if reference:
            vals['reference'] = '%s,%s' % (reference._name, reference.id)
        self.env['bf.gamification.xp.transaction'].sudo().create(vals)

        # Recompute XP and level
        self._compute_xp()
        self._compute_level()

        # Update activity date and streak
        today = fields.Date.today()
        if self.last_activity_date != today:
            streak_reset_days = int(self.env['ir.config_parameter'].sudo().get_param(
                'bf_gamification.streak_reset_days', '2'))
            if self.last_activity_date and (today - self.last_activity_date).days <= streak_reset_days:
                self.current_streak += 1
            else:
                self.current_streak = 1
            if self.current_streak > self.longest_streak:
                self.longest_streak = self.current_streak
            self.last_activity_date = today

        # Check for level-up
        new_level = self.level_id
        if new_level and old_level and new_level.id != old_level.id:
            self._notify_level_up(old_level, new_level)

        # Check automatic badges
        self._check_automatic_badges()

    def _notify_level_up(self, old_level, new_level):
        """Send level-up notification via bus."""
        if not self.env['ir.config_parameter'].sudo().get_param(
                'bf_gamification.popup_enabled', 'True') == 'True':
            return
        self.env['bus.bus']._sendone(
            self.user_id.partner_id,
            'bf_gamification/level_up',
            {
                'old_level': {'name': old_level.name, 'title': old_level.title},
                'new_level': {
                    'name': new_level.name,
                    'title': new_level.title,
                    'css_class': new_level.css_class or '',
                },
                'total_xp': self.total_xp,
            },
        )

    def _notify_badge_earned(self, badge):
        """Send badge earned notification via bus."""
        if not self.env['ir.config_parameter'].sudo().get_param(
                'bf_gamification.popup_enabled', 'True') == 'True':
            return
        self.env['bus.bus']._sendone(
            self.user_id.partner_id,
            'bf_gamification/badge_earned',
            {
                'badge_name': badge.name,
                'badge_description': badge.description or '',
                'xp_reward': badge.xp_reward,
                'rarity': badge.rarity,
                'popup_effect': badge.popup_effect,
                'popup_sound': badge.popup_sound,
                'popup_message': badge.popup_message or '',
            },
        )

    def _check_automatic_badges(self):
        """Check and award automatic/threshold badges."""
        Badge = self.env['bf.gamification.badge']
        UserBadge = self.env['bf.gamification.user.badge']

        auto_badges = Badge.search([
            ('condition_type', 'in', ['automatic', 'threshold']),
            ('active', '=', True),
        ])
        for badge in auto_badges:
            # Skip if already earned and unique
            if badge.unique:
                existing = UserBadge.search([
                    ('user_id', '=', self.user_id.id),
                    ('badge_id', '=', badge.id),
                ], limit=1)
                if existing:
                    continue

            awarded = False
            if badge.condition_type == 'threshold' and badge.condition_threshold:
                check_field = badge.threshold_field or 'total_xp'
                value = getattr(self, check_field, 0)
                if value >= badge.condition_threshold:
                    awarded = True
            elif (badge.condition_type == 'automatic' and badge.condition_model
                  and badge.condition_domain
                  and badge.condition_model in ALLOWED_CONDITION_MODELS):
                try:
                    domain = safe_eval(
                        badge.condition_domain,
                        {"uid": self.user_id.id},
                        mode="eval", nocopy=True,
                    )
                    if not isinstance(domain, list):
                        raise ValueError("Domain must be a list")
                    user_field = badge.condition_user_field or 'create_uid'
                    domain.append((user_field, '=', self.user_id.id))
                    count = self.env[badge.condition_model].sudo().search_count(domain)
                    if count >= (badge.condition_threshold or 1):
                        awarded = True
                except Exception:
                    _logger.warning(
                        "Badge %s: invalid condition_domain in profile check: %s",
                        badge.name, badge.condition_domain,
                    )

            if awarded:
                self._grant_badge(badge)

    def _grant_badge(self, badge, granted_by=None, note=None):
        """Grant a badge to this profile's user."""
        UserBadge = self.env['bf.gamification.user.badge']
        if badge.unique:
            existing = UserBadge.search([
                ('user_id', '=', self.user_id.id),
                ('badge_id', '=', badge.id),
            ], limit=1)
            if existing:
                return existing

        user_badge = UserBadge.sudo().create({
            'user_id': self.user_id.id,
            'badge_id': badge.id,
            'granted_by': granted_by.id if granted_by else False,
            'note': note,
        })

        # Award XP for the badge
        if badge.xp_reward:
            self.env['bf.gamification.xp.transaction'].sudo().create({
                'user_id': self.user_id.id,
                'xp_amount': badge.xp_reward,
                'source': 'badge',
                'description': 'Badge obtenu : %s' % badge.name,
                'reference': 'bf.gamification.badge,%s' % badge.id,
            })
            self._compute_xp()
            self._compute_level()

        # Notify
        self._notify_badge_earned(badge)

        return user_badge

    @api.model
    def _cron_update_streaks(self):
        """Cron job to reset streaks for inactive users."""
        streak_reset_days = int(self.env['ir.config_parameter'].sudo().get_param(
            'bf_gamification.streak_reset_days', '2'))
        cutoff = fields.Date.today() - timedelta(days=streak_reset_days)
        profiles = self.search([
            ('current_streak', '>', 0),
            ('last_activity_date', '<', cutoff),
        ])
        profiles.write({'current_streak': 0})

    @api.model
    def _cron_check_badges(self):
        """Cron job to check and award automatic badges for all users."""
        profiles = self.search([])
        for profile in profiles:
            profile._check_automatic_badges()

    @api.model
    def _backfill_recent_xp(self, hours=96):
        """Retroactively award XP for recent work done before gamification was active.

        Scans timesheets, completed tasks, documents, messages, and resolved
        helpdesk tickets from the last ``hours`` hours. Skips records that
        already have an XP transaction to avoid duplicates.
        """
        cutoff = datetime.now() - timedelta(hours=hours)
        cutoff_str = cutoff.strftime('%Y-%m-%d %H:%M:%S')
        Txn = self.env['bf.gamification.xp.transaction'].sudo()
        Rule = self.env['bf.gamification.xp.rule']

        # Pre-load existing references for fast dedup
        existing_refs = set(Txn.search([
            ('date', '>=', cutoff_str),
        ]).mapped('reference'))

        def _has_txn(ref_str):
            return ref_str in existing_refs

        created = 0

        # --- Timesheets ---
        hourly_rule = Rule.search([
            ('source', '=', 'timesheet'), ('trigger', '=', 'create'),
            ('active', '=', True),
        ], limit=1)
        if hourly_rule:
            lines = self.env['account.analytic.line'].search([
                ('create_date', '>=', cutoff_str),
                ('project_id', '!=', False),
                ('unit_amount', '>', 0),
            ])
            for line in lines:
                user = line.user_id or line.create_uid
                ref = 'account.analytic.line,%s' % line.id
                if _has_txn(ref):
                    continue
                xp = int(line.unit_amount * hourly_rule.xp_amount)
                if xp > 0:
                    Txn.create({
                        'user_id': user.id,
                        'xp_amount': xp,
                        'source': 'timesheet',
                        'description': '%.1fh feuille de temps (r\u00e9troactif)' % line.unit_amount,
                        'reference': ref,
                        'date': line.create_date,
                    })
                    created += 1

        # --- Tasks completed ---
        task_rule = Rule.search([
            ('source', '=', 'task'), ('trigger', '=', 'complete'),
            ('active', '=', True), ('min_value', '=', 0),
        ], limit=1)
        if task_rule:
            tasks = self.env['project.task'].search([
                ('write_date', '>=', cutoff_str),
                ('stage_id.fold', '=', True),
            ])
            for task in tasks:
                user = task.user_ids[:1] if task.user_ids else task.create_uid
                if not user:
                    continue
                ref = 'project.task,%s' % task.id
                if _has_txn(ref):
                    continue
                Txn.create({
                    'user_id': user.id,
                    'xp_amount': task_rule.xp_amount,
                    'source': 'task',
                    'description': 'T\u00e2che compl\u00e9t\u00e9e : %s (r\u00e9troactif)' % task.name,
                    'reference': ref,
                    'date': task.write_date,
                })
                created += 1

        # --- Documents created ---
        doc_rule = Rule.search([
            ('source', '=', 'document'), ('trigger', '=', 'create'),
            ('active', '=', True),
        ], limit=1)
        if doc_rule:
            docs = self.env['project.document'].search([
                ('create_date', '>=', cutoff_str),
            ])
            for doc in docs:
                ref = 'project.document,%s' % doc.id
                if _has_txn(ref):
                    continue
                Txn.create({
                    'user_id': doc.create_uid.id,
                    'xp_amount': doc_rule.xp_amount,
                    'source': 'document',
                    'description': 'Document cr\u00e9\u00e9 : %s (r\u00e9troactif)' % doc.name,
                    'reference': ref,
                    'date': doc.create_date,
                })
                created += 1

        # --- Messages posted ---
        msg_rule = Rule.search([
            ('source', '=', 'message'), ('trigger', '=', 'create'),
            ('active', '=', True),
        ], limit=1)
        if msg_rule:
            messages = self.env['mail.message'].search([
                ('create_date', '>=', cutoff_str),
                ('message_type', '=', 'comment'),
                ('body', '!=', False),
                ('author_id', '!=', False),
            ])
            for msg in messages:
                user = self.env['res.users'].sudo().search([
                    ('partner_id', '=', msg.author_id.id),
                    ('active', '=', True),
                ], limit=1)
                if not user:
                    continue
                ref = 'mail.message,%s' % msg.id
                if _has_txn(ref):
                    continue
                Txn.create({
                    'user_id': user.id,
                    'xp_amount': msg_rule.xp_amount,
                    'source': 'message',
                    'description': 'Message post\u00e9 (r\u00e9troactif)',
                    'reference': ref,
                    'date': msg.create_date,
                })
                created += 1

        # --- Helpdesk tickets resolved ---
        ticket_rule = Rule.search([
            ('source', '=', 'helpdesk'), ('trigger', '=', 'complete'),
            ('active', '=', True),
        ], limit=1)
        if ticket_rule:
            try:
                tickets = self.env['helpdesk.ticket'].search([
                    ('write_date', '>=', cutoff_str),
                    ('stage_id.fold', '=', True),
                ])
                for ticket in tickets:
                    user = ticket.user_id or ticket.create_uid
                    if not user:
                        continue
                    ref = 'helpdesk.ticket,%s' % ticket.id
                    if _has_txn(ref):
                        continue
                    Txn.create({
                        'user_id': user.id,
                        'xp_amount': ticket_rule.xp_amount,
                        'source': 'helpdesk',
                        'description': 'Ticket r\u00e9solu : %s (r\u00e9troactif)' % ticket.name,
                        'reference': ref,
                        'date': ticket.write_date,
                    })
                    created += 1
            except Exception:
                _logger.warning("Fox Quest: helpdesk backfill skipped", exc_info=True)

        # --- Recompute profiles ---
        if created:
            # Collect all users who got transactions
            user_ids = Txn.search([
                ('date', '>=', cutoff_str),
            ]).mapped('user_id').ids
            profiles = self.search([('user_id', 'in', user_ids)])
            for profile in profiles:
                profile._compute_xp()
                profile._compute_level()
                profile._check_automatic_badges()

        _logger.info(
            "Fox Quest: backfill completed — %d XP transactions created for the last %d hours",
            created, hours,
        )
