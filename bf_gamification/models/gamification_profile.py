import logging
from datetime import datetime, timedelta

from psycopg2 import errors

from odoo import SUPERUSER_ID, _, api, fields, models
from odoo.exceptions import AccessError, ValidationError
from odoo.http import request
from odoo.tools.misc import clean_context
from odoo.tools.safe_eval import safe_eval

from . import fq_days, fq_savepoint
from .gamification_badge import ALLOWED_CONDITION_MODELS

_logger = logging.getLogger(__name__)

# Ce qui ne fait pas un jour de constance : ce n'est pas du travail
# (badge, récompense), ou ça découle déjà de la constance ou d'un geste compté
# ailleurs (la constance elle-même, la fête « à jour »).
CONSTANCE_EXCLUDED_SOURCES = ('badge', 'reward', 'streak', 'caught_up')
# Clé que `bf_impersonate` pose dans la session quand le support voit Symbifox
# sous le compte d'une personne. Lue telle quelle : Fox Quest ne
# dépend pas de ce module, et sans lui la clé n'existe jamais.
IMPERSONATION_SESSION_KEY = 'bf_impersonate'


class GamificationProfile(models.Model):
    _name = 'bf.gamification.profile'
    _description = 'Profil Fox Quest'
    _order = 'total_xp desc'

    user_id = fields.Many2one('res.users', string="Utilisateur", required=True,
                              ondelete='cascade', index=True)
    avatar = fields.Image(string="Avatar", max_width=256, max_height=256)
    # total_xp est l'XP GAGNÉ : il fixe le niveau, le classement et les badges à
    # seuil. Ce qu'on dépense à la boutique sort de xp_balance seulement : une
    # récompense ne coûte jamais un niveau.
    total_xp = fields.Integer(string="XP total", compute='_compute_xp', store=True,
                              help="XP gagné. Fixe le niveau, le classement et les badges à seuil.")
    xp_balance = fields.Integer(string="Solde XP", compute='_compute_xp', store=True,
                                help="XP gagné moins les récompenses réclamées : ce qu'on peut encore dépenser.")
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

    # Jours de congé de constance. La participation ne se pose que
    # par la personne elle-même (`set_leave_opt_in` du tableau de bord), et
    # ni elle ni sa progression ne se lisent par les collègues : le profil,
    # lui, est lisible de tous (classement).
    leave_opt_in = fields.Boolean(
        string="Participe aux congés de constance", readonly=True,
        groups='bf_gamification.group_gamification_manager')
    leave_opt_in_date = fields.Datetime(
        string="Participation activée le", readonly=True,
        groups='bf_gamification.group_gamification_manager')
    leave_opt_out_date = fields.Datetime(
        string="Participation retirée le", readonly=True,
        groups='bf_gamification.group_gamification_manager')
    leave_progress = fields.Integer(
        string="Jours ouvrables vers le prochain congé", readonly=True,
        groups='bf_gamification.group_gamification_manager')
    # La marche se recompte depuis l'historique ; le dernier palier atteint, lui,
    # ne se recompte jamais : pas deux demandes pour le même palier.
    leave_last_step_day = fields.Date(
        string="Dernier palier atteint le", readonly=True,
        groups='bf_gamification.group_gamification_manager')

    _sql_constraints = [
        ('user_unique', 'UNIQUE(user_id)', 'Un seul profil par utilisateur.'),
    ]

    # `readonly` n'arrête que le formulaire : un gestionnaire Fox Quest, qui a
    # le droit d'écrire les profils, posait par RPC la participation ou la
    # marche d'un collègue. Seul le code de Fox Quest, en sudo, y touche.
    def _bf_check_leave_fields(self, names):
        if not self.env.su and any(name.startswith('leave_') for name in names):
            raise AccessError(_("La participation aux congés de constance et sa progression "
                                "ne se modifient pas à la main."))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            self._bf_check_leave_fields(vals)
        if not self.env.su:
            # La garde ne lit que `vals` : un `default_leave_opt_in` du contexte de
            # l'appelant posait la participation d'un collègue.
            return super(GamificationProfile, self.with_context(
                clean_context(self.env.context))).create(vals_list)
        return super().create(vals_list)

    def write(self, vals):
        self._bf_check_leave_fields(vals)
        return super().write(vals)

    @api.constrains('showcase_badge_ids')
    def _check_showcase_limit(self):
        for profile in self:
            if len(profile.showcase_badge_ids) > 5:
                raise ValidationError("La vitrine ne peut contenir que 5 badges maximum.")

    @api.depends('user_id')
    def _compute_xp(self):
        Txn = self.env['bf.gamification.xp.transaction']
        for profile in self:
            transactions = Txn.search([('user_id', '=', profile.user_id.id)])
            earned = sum(t.xp_amount for t in transactions if t.source != 'reward')
            profile.total_xp = max(earned, 0)
            profile.xp_balance = max(sum(transactions.mapped('xp_amount')), 0)

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

    @api.model
    def _bf_player_domain(self):
        """Les profils qui jouent : ni compte de service, ni portail."""
        return [('user_id.share', '=', False),
                ('user_id.bf_gamification_excluded', '=', False),
                ('user_id', '!=', SUPERUSER_ID)]

    @api.model
    def _bf_is_player(self, user):
        """Un humain de l'interne. Un message posté par le système au nom d'un
        client portail lui faisait gagner niveaux et badges, et le
        robot des rencontres était 2e au classement."""
        user = user.sudo()
        return (bool(user) and user.id != SUPERUSER_ID and user.active
                and not user.share and not user.bf_gamification_excluded)

    @api.model
    def _bf_is_interactive(self):
        """Le geste vient-il d'une personne, dans une requête ?

        Les crons d'ingestion et de miroir IMAP tournent souvent sous le compte
        d'une personne : se fier à `env.user` faisait sortir le renard plusieurs
        fois par jour et comptait des jours de constance sans elle. Un cron, un
        import ou un script lancé hors d'Odoo n'ont pas de requête (un appel
        XML-RPC, d'un script ou d'un assistant, en a une : il compte comme le geste
        de la personne dont il porte le compte). Une méthode, et non une clé de contexte : un appel RPC poserait
        la clé lui-même pour se donner des points.

        Sous usurpation (`bf_impersonate`, le support voit l'écran de la personne),
        la requête et l'identité sont celles de la personne, mais le geste est
        celui du support : il ne fait ni jour de constance ni renard.
        """
        return bool(request) and not self._bf_is_impersonated()

    @api.model
    def _bf_is_impersonated(self):
        """La requête sert-elle une personne sous usurpation du support ?

        On ne cherche que la marque de la session, sans comparer l'identité à la
        charge : `bf_impersonate` retire lui-même une charge périmée avant que le
        code métier ne s'exécute, et dans le doute on refuse plutôt que de laisser
        passer le geste de quelqu'un d'autre.
        """
        try:
            session = request.session if request else None
        except RuntimeError:
            return False
        return bool(session is not None and session.get(IMPERSONATION_SESSION_KEY))

    @api.model
    def _bf_refuse_if_impersonated(self, message):
        if self._bf_is_impersonated():
            raise AccessError(message)

    @api.model
    def _bf_is_own_gesture(self, user):
        """Le geste de la personne ELLE-MÊME, dans une requête.

        Une requête ne suffit pas : le robot des rencontres (XML-RPC) qui fait
        passer le CR d'une personne en « Révisé », un collègue qui ferme sa tâche ou
        fait son activité agissent dans LEUR requête. Leur geste crédite encore
        la personne, mais ne lui fait ni un jour de constance ni sortir son renard.
        """
        return bool(user) and self.env.uid == user.id and self._bf_is_interactive()

    def _compute_rank(self):
        all_profiles = self.search(self._bf_player_domain(), order='total_xp desc')
        rank_map = {p.id: idx + 1 for idx, p in enumerate(all_profiles)}
        for profile in self:
            profile.rank = rank_map.get(profile.id, 0)

    def _compute_xp_periods(self):
        for profile in self:
            # La semaine et le mois de la personne, pas ceux du serveur.
            user = profile.user_id
            today = fq_days.user_today(user) or fields.Date.today()
            week_start = today - timedelta(days=today.weekday())
            month_start = today.replace(day=1)
            week_txns = self.env['bf.gamification.xp.transaction'].search([
                ('user_id', '=', user.id),
                ('date', '>=', fq_days.local_midnight_utc(user, week_start)),
            ])
            profile.xp_this_week = sum(week_txns.mapped('xp_amount'))
            month_txns = self.env['bf.gamification.xp.transaction'].search([
                ('user_id', '=', user.id),
                ('date', '>=', fq_days.local_midnight_utc(user, month_start)),
            ])
            profile.xp_this_month = sum(month_txns.mapped('xp_amount'))

    def _get_or_create_profile(self, user):
        """Get or create a gamification profile for the given user."""
        # En sudo : les points se créditent depuis le travail de n'importe qui
        # (l'auteur d'un message, le responsable d'une tâche), et un usager n'a
        # plus le droit d'écrire un profil, pas même le sien (il pouvait
        # réécrire son XP total stocké).
        Profile = self.sudo()
        profile = Profile.search([('user_id', '=', user.id)], limit=1)
        if not profile:
            # À la première visite, la barre du haut et le tableau de bord
            # créaient le profil en même temps : la seconde requête butait sur
            # l'unicité, qu'Odoo ne rejoue pas, et l'usager voyait une erreur
            # (vu en essai). En lecture répétable, ON CONFLICT lève à la
            # place une erreur de sérialisation : Odoo rejoue la requête, qui
            # trouve alors le profil créé par l'autre.
            self.env.flush_all()
            self.env.cr.execute("""
                INSERT INTO bf_gamification_profile
                       (user_id, current_streak, longest_streak,
                        create_uid, create_date, write_uid, write_date)
                VALUES (%s, 0, 0, %s, now() AT TIME ZONE 'UTC', %s, now() AT TIME ZONE 'UTC')
                ON CONFLICT (user_id) DO NOTHING
                RETURNING id
            """, (user.id, self.env.uid, self.env.uid))
            row = self.env.cr.fetchone()
            if row:
                profile = Profile.browse(row[0])
                profile._compute_xp()
                profile._compute_level()
            else:
                profile = Profile.search([('user_id', '=', user.id)], limit=1)
        return profile

    def _award_xp(self, amount, source, description, reference=None):
        """Central method to award XP. Creates transaction, recalculates level,
        checks badges, and triggers popups if applicable."""
        self.ensure_one()
        # Check if gamification is enabled
        if not self.env['ir.config_parameter'].sudo().get_param(
                'bf_gamification.gamification_enabled', 'True') == 'True':
            return
        if not self._bf_is_player(self.user_id):
            return

        old_level = self.level_id

        # Date et « née d'un geste » posées par le journal, pas par le contexte
        # de l'appelant ; seul le geste de la personne
        # elle-même est « né d'un geste ».
        txn = self.env['bf.gamification.xp.transaction']._bf_create_entry(
            self.user_id, amount, source, description, reference=reference)

        # La constance d'abord : elle peut verser l'XP du streak. Seul un geste
        # de travail, dans une requête, fait un jour de constance.
        if txn.from_request and amount > 0 and source not in CONSTANCE_EXCLUDED_SOURCES:
            self._bf_mark_active_day()

        # Recompute XP and level
        self._compute_xp()
        self._compute_level()

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
                with fq_savepoint.tolerated("domaine du badge %s : %s" % (
                        badge.name, badge.condition_domain)):
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

        # Sans contexte de l'appelant : un `default_date_earned` passé par RPC à un
        # geste antidatait le badge.
        user_badge = UserBadge.sudo().with_context(clean_context(self.env.context)).create({
            'user_id': self.user_id.id,
            'badge_id': badge.id,
            'granted_by': granted_by.id if granted_by else False,
            'note': note,
        })

        # Award XP for the badge
        if badge.xp_reward:
            self.env['bf.gamification.xp.transaction']._bf_create_entry(
                self.user_id, badge.xp_reward, 'badge', 'Badge obtenu : %s' % badge.name,
                reference=badge)
            self._compute_xp()
            self._compute_level()

        # Notify
        self._notify_badge_earned(badge)

        return user_badge

    @api.model
    def _cron_update_streaks(self):
        """Refait chaque jour la constance depuis l'historique.

        Avant la 18.0.3.0.0, le seuil se lisait en jours de calendrier : toute fin de
        semaine de congé cassait le streak. On ne compte plus que les jours
        ouvrables de la personne, et seulement ceux qui sont finis chez elle.
        Refaite et non plus remise à zéro : une absence déclarée après coup
        répare ce qu'une nuit avait cassé.
        """
        profiles = self.search(self._bf_player_domain()) | self.search([('current_streak', '>', 0)])
        for profile in profiles:
            # Chaque profil à part, conflits de concurrence compris : rien ne
            # rejoue un cron, et un profil en conflit n'annule pas les autres
            #.
            with fq_savepoint.isolated(self.env, "constance de %s" % profile.user_id.login,
                                       replay=False):
                profile._bf_recompute_constance()

    @api.model
    def _cron_check_badges(self):
        """Cron job to check and award automatic badges for all users."""
        profiles = self.search(self._bf_player_domain())
        for profile in profiles:
            profile._check_automatic_badges()

    # ------------------------------------------------------------------
    # Constance en jours ouvrables
    # ------------------------------------------------------------------

    def _bf_streak_tolerance(self):
        """Jours ouvrables manqués tolérés. La clé garde son sens d'avant :
        2 = un jour manqué ne casse pas la constance."""
        try:
            days = int(self.env['ir.config_parameter'].sudo().get_param(
                'bf_gamification.streak_reset_days', '2'))
        except ValueError:
            days = 2
        return max(days - 1, 0)

    def _bf_mark_active_day(self):
        """Le premier geste du jour refait la constance depuis l'historique.

        Un jour non ouvrable (fin de semaine, férié, absence déclarée) ne monte
        ni ne casse le compteur, et ne devient pas la « dernière activité » :
        travailler le samedi ne rapporte pas de constance.
        """
        self.ensure_one()
        user = self.user_id
        today = fq_days.user_today(user)
        last = self.last_activity_date
        if last and last >= today:
            return
        if not fq_days.is_working_day(self.env, user, today):
            return
        # Le geste qui verse l'XP (poster un message, fermer une tâche) passe
        # même si la constance échoue (comme la fête « à jour »).
        with fq_savepoint.isolated(self.env, "constance de %s" % user.login):
            self._bf_recompute_constance()
            if self.last_activity_date == today and self.current_streak > 1:
                self._bf_pay_streak_rule()

    def _bf_pay_streak_rule(self):
        """« Streak quotidien » : une fois par jour ouvrable où la constance continue."""
        self.ensure_one()
        user = self.user_id
        rule = self.env['bf.gamification.xp.rule'].search([
            ('source', '=', 'streak'), ('trigger', '=', 'daily'),
            ('active', '=', True)], limit=1)
        if not rule or not rule.xp_amount:
            return
        # Une fois par jour : `_bf_mark_active_day` ne revient qu'au premier
        # geste d'un jour encore non compté.
        self.env['bf.gamification.xp.transaction']._bf_create_entry(
            user, rule.xp_amount, 'streak',
            'Constance : %d jours ouvrables' % self.current_streak)

    def _bf_active_days(self):
        """Les jours de la personne qui portent de l'XP de travail né d'un geste.

        En SQL, dans le fuseau de la personne : une année d'historique, c'est
        quelques milliers de transactions, mais au plus 365 jours.
        """
        self.ensure_one()
        user = self.user_id
        self.env['bf.gamification.xp.transaction'].flush_model(
            ['user_id', 'xp_amount', 'source', 'date', 'from_request'])
        where = """
              FROM bf_gamification_xp_transaction
             WHERE user_id = %s AND xp_amount > 0 AND from_request IS TRUE
               AND date IS NOT NULL AND source NOT IN %s
        """
        params = (user.id, CONSTANCE_EXCLUDED_SOURCES)
        try:
            with self.env.cr.savepoint(flush=False):
                self.env.cr.execute(
                    "SELECT DISTINCT (date AT TIME ZONE 'UTC' AT TIME ZONE %s)::date AS day"
                    + where + " ORDER BY day", (fq_days.user_tz(user).zone,) + params,
                    log_exceptions=False)
                return [row[0] for row in self.env.cr.fetchall()]
        except errors.InvalidParameterValue:
            # Un fuseau que pytz connaît et Postgres pas (sa base de fuseaux est
            # la sienne) : on convertit ici, plus lentement, sans faire échouer
            # le geste ni la migration.
            self.env.cr.execute("SELECT DISTINCT date" + where, params)
            return sorted({fq_days.local_date(user, row[0]) for row in self.env.cr.fetchall()})

    def _bf_recompute_constance(self):
        """Refait constance et marche vers le congé depuis l'historique, en jours ouvrables.

        Dérivées, et non plus incrémentées : une absence déclarée après
        coup (malade lundi et mardi, déclaré mercredi) ou un congé validé en
        retard répare la constance et la marche, au lieu de les laisser cassées
        par le premier XP du mercredi. Refaite au premier geste du jour, par le
        cron, et quand une absence ou un congé change. Sert aussi à la migration
        vers 18.0.3.0.0 : le streak d'avant comptait des jours de calendrier.
        """
        for profile in self.sudo():
            user = profile.user_id
            today = fq_days.user_today(user)
            days = [day for day in profile._bf_active_days() if day <= today]
            run, longest, last = [], 0, False
            if days:
                off = fq_days.non_working_days(self.env, user, days[0], today)
                tolerance = profile._bf_streak_tolerance()

                def missed(first, then):
                    return sum(1 for n in range(1, (then - first).days)
                               if first + timedelta(days=n) not in off)

                for day in days:
                    if day in off:
                        continue
                    if last and missed(last, day) <= tolerance:
                        run.append(day)
                    else:
                        run = [day]
                    longest = max(longest, len(run))
                    last = day
                if last and last < today and missed(last, today) > tolerance:
                    run = []
            vals = {'current_streak': len(run), 'longest_streak': longest,
                    'last_activity_date': last}
            changed = {name: value for name, value in vals.items() if profile[name] != value}
            if changed:
                profile.write(changed)
            if self._bf_is_player(user):
                profile._bf_leave_sync(run)

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

        def credit(user, amount, source, description, ref, when):
            # Daté du travail rattrapé, et jamais « né d'un geste » : posé à
            # l'installation, il ne fait pas de jour de constance.
            Txn._bf_create_entry(user, amount, source, description, reference=ref,
                                 date=when, from_request=False)

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
                    credit(user, xp, 'timesheet',
                           '%.1fh feuille de temps (r\u00e9troactif)' % line.unit_amount,
                           ref, line.create_date)
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
                credit(user, task_rule.xp_amount, 'task',
                       'T\u00e2che compl\u00e9t\u00e9e : %s (r\u00e9troactif)' % task.name,
                       ref, task.write_date)
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
                credit(doc.create_uid, doc_rule.xp_amount, 'document',
                       'Document cr\u00e9\u00e9 : %s (r\u00e9troactif)' % doc.name,
                       ref, doc.create_date)
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
                credit(user, msg_rule.xp_amount, 'message',
                       'Message post\u00e9 (r\u00e9troactif)', ref, msg.create_date)
                created += 1

        # --- Helpdesk tickets resolved ---
        ticket_rule = Rule.search([
            ('source', '=', 'helpdesk'), ('trigger', '=', 'complete'),
            ('active', '=', True),
        ], limit=1)
        if ticket_rule:
            with fq_savepoint.tolerated("rattrapage des tickets"):
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
                    credit(user, ticket_rule.xp_amount, 'helpdesk',
                           'Ticket r\u00e9solu : %s (r\u00e9troactif)' % ticket.name,
                           ref, ticket.write_date)
                    created += 1

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
