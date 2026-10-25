"""Le renard qui félicite quand on est à jour, et les jours de congé de constance.

À jour : cinq listes de travail de la personne. Quand un geste de la personne
elle-même vide une liste, Fox Quest le fête une fois par jour et par liste :
l'effet « rainbow man » du cœur (celui de la fin d'un rapprochement bancaire),
avec un renard, et un petit bonus d'XP. Les cinq vides à la fois : une fête plus
grande, une fois par jour. Rien ne sort d'un cron, d'une règle de tri, d'un compte
de service ni d'une usurpation du support : seul compte le geste de la personne
dont c'est la liste, dans sa propre requête. Un script ou un assistant qui appelle
Odoo sous son compte (XML-RPC) fait une requête comme une autre : son appel compte
comme son geste.

Congés de constance : éteints par défaut. La société les allume et fixe le
palier ; chaque employé choisit d'y participer. Le palier atteint ouvre une
réclamation en attente, qu'un gestionnaire approuve ou refuse : aucun jour
n'est accordé sans une personne qui décide (art. 12.1 de la loi sur le privé).
"""
import logging
from datetime import date, timedelta

from odoo import SUPERUSER_ID, api, fields, models
from odoo.exceptions import UserError
from odoo.tools.misc import clean_context

from . import fq_days, fq_savepoint

_logger = logging.getLogger(__name__)

CAUGHT_UP_KINDS = ('activity', 'inbox', 'task', 'report', 'agenda')
CAUGHT_UP_MESSAGES = {
    'activity': "Plus aucune activité en retard ni pour aujourd'hui. Bien joué !",
    'inbox': "Boîte de réception vide. Inbox Zero !",
    'task': "Plus aucune tâche en retard.",
    'report': "Tous vos comptes rendus sont envoyés.",
    'agenda': "Vos ordres du jour des 48 prochaines heures sont envoyés.",
    'all': "Tout est à jour : activités, courriels, tâches, comptes rendus et ordres du jour. Bravo !",
}
FOX_IMAGE = '/bf_gamification/static/img/renard_a_jour.svg'
# Un CR plus vieux que ça ne compte plus comme « à envoyer » : un vieux CR
# oublié ne doit pas empêcher d'être à jour pour toujours.
REPORT_LOOKBACK_DAYS = 30
AGENDA_HORIZON_HOURS = 48
# Marque de contexte posée par les moteurs de la boîte courriel (ingestion,
# règles, synchronisation) : ce qu'ils traitent n'est pas un geste de la
# personne, même lancés depuis sa requête. Elle ne peut que
# couper la fête : la poser par RPC ne rapporte rien.
NO_CELEBRATION = 'bf_gamification_no_celebration'


class GamificationCaughtUp(models.Model):
    _name = 'bf.gamification.caught.up'
    _description = 'Journée à jour Fox Quest'
    _order = 'day desc, id desc'

    user_id = fields.Many2one('res.users', string="Utilisateur", required=True,
                              ondelete='cascade', index=True)
    kind = fields.Selection([
        ('activity', 'Activités'),
        ('inbox', 'Courriels'),
        ('task', 'Tâches'),
        ('report', 'Comptes rendus'),
        ('agenda', 'Ordres du jour'),
        ('all', 'Tout'),
    ], string="Liste", required=True)
    day = fields.Date(string="Jour", required=True,
                      help="Le jour de la personne, dans son fuseau.")

    _sql_constraints = [
        ('one_a_day', 'UNIQUE(user_id, kind, day)',
         "Une seule fête par liste et par jour."),
    ]


class GamificationProfileConstance(models.Model):
    _inherit = 'bf.gamification.profile'

    # ------------------------------------------------------------------
    # À jour
    # ------------------------------------------------------------------

    @api.model
    def _bf_pending_count(self, user, kind):
        """Combien d'éléments restent dans la liste `kind` de la personne."""
        today = fq_days.user_today(user)
        now = fields.Datetime.now()
        if kind == 'activity':
            return self.env['mail.activity'].sudo().search_count([
                ('user_id', '=', user.id), ('date_deadline', '<=', today)])
        if kind == 'inbox':
            BfEmail = self.env['bf.email'].sudo()
            return BfEmail.search_count([('user_id', '=', user.id)] + BfEmail._inbox_domain())
        if kind == 'task':
            return self.env['project.task'].sudo().search_count([
                ('user_ids', 'in', user.id),
                ('state', 'not in', ('1_done', '1_canceled')),
                # Une tâche dans une étape repliée est fermée, même si son état
                # n'a pas suivi.
                '|', ('stage_id', '=', False), ('stage_id.fold', '=', False),
                ('date_deadline', '!=', False),
                ('date_deadline', '<', fq_days.local_midnight_utc(user, today)),
            ])
        if kind == 'report':
            # Seuls les CR de rencontres PLANIFIÉES (liées à l'agenda) : les
            # appels non planifiés (numéros inconnus, messagerie vocale) font
            # l'essentiel des brouillons et ne s'envoient pas.
            records = self.env['meeting.record'].sudo().search([
                ('organizer_id', '=', user.id),
                ('calendar_event_id', '!=', False),
                ('report_state', '!=', 'sent'),
                ('date', '>=', now - timedelta(days=REPORT_LOOKBACK_DAYS)),
                ('date', '<', now),
            ])
            return len(records.filtered(
                lambda r: r.date + timedelta(hours=r.date_delay or 0) <= now))
        if kind == 'agenda':
            return self.env['meeting.agenda'].sudo().search_count([
                ('organizer_id', '=', user.id),
                ('state', '!=', 'cancelled'),
                ('email_sent_date', '=', False),
                ('date', '>=', now),
                ('date', '<=', now + timedelta(hours=AGENDA_HORIZON_HOURS)),
            ])
        return 0

    @api.model
    def _bf_caught_up_check(self, user, kind):
        """Après un geste de la personne : sa liste `kind` vient-elle de se vider ?

        Ne casse jamais le geste qui l'a provoquée : une erreur est annulée
        jusqu'à un point de sauvegarde, au lieu d'avorter la transaction du geste
        ; une erreur de concurrence remonte, pour qu'Odoo rejoue.
        """
        # Le geste doit être celui de la personne, dans une requête : une règle
        # de tri, un cron (ceux de la boîte courriel tournent souvent sous le
        # compte d'une personne) ou un robot qui vide sa liste ne fait pas sortir
        # le renard.
        if not user or self.env.context.get('install_mode') or self.env.context.get(NO_CELEBRATION):
            return False
        if not self._bf_is_own_gesture(user):
            return False
        celebrated = False
        with fq_savepoint.isolated(self.env, "vérification « à jour » (%s)" % kind):
            celebrated = self._bf_caught_up_celebrate(user, kind)
        return celebrated

    @api.model
    def _bf_caught_up_celebrate(self, user, kind):
        ICP = self.env['ir.config_parameter'].sudo()
        if ICP.get_param('bf_gamification.gamification_enabled', 'True') != 'True':
            return False
        if not self._bf_is_player(user):
            return False
        # Les fêtes du jour d'abord, la liste ensuite : compter la boîte ou les
        # tâches coûte plus cher que lire une ligne, et après la première fête
        # du jour, chaque geste de la journée passait par là.
        today = fq_days.user_today(user)
        CaughtUp = self.env['bf.gamification.caught.up'].sudo()
        done_today = set(CaughtUp.search([
            ('user_id', '=', user.id), ('day', '=', today)]).mapped('kind'))
        if kind in done_today:
            return False
        if self._bf_pending_count(user, kind):
            return False
        profile = self._get_or_create_profile(user)
        mark = CaughtUp.create({'user_id': user.id, 'kind': kind, 'day': today})
        profile._bf_award_rule('bf_gamification.xp_rule_caught_up', 'caught_up',
                               'À jour : %s' % dict(mark._fields['kind'].selection)[kind],
                               mark)
        celebrate = kind
        if 'all' not in done_today and not any(
                self._bf_pending_count(user, other)
                for other in CAUGHT_UP_KINDS if other != kind):
            mark_all = CaughtUp.create({'user_id': user.id, 'kind': 'all', 'day': today})
            profile._bf_award_rule('bf_gamification.xp_rule_caught_up_all', 'caught_up',
                                   'Tout est à jour', mark_all)
            celebrate = 'all'
        profile._bf_notify_caught_up(celebrate)
        return celebrate

    def _bf_award_rule(self, xmlid, source, description, reference=None):
        """Verse l'XP d'une règle désignée par son identifiant, si elle est active."""
        self.ensure_one()
        rule = self.env.ref(xmlid, raise_if_not_found=False)
        if rule and rule.active and rule.xp_amount:
            self._award_xp(rule.xp_amount, source, description, reference=reference)

    def _bf_notify_caught_up(self, kind):
        self.ensure_one()
        if self.env['ir.config_parameter'].sudo().get_param(
                'bf_gamification.popup_enabled', 'True') != 'True':
            return
        self.env['bus.bus']._sendone(
            self.user_id.partner_id,
            'bf_gamification/caught_up',
            {
                'kind': kind,
                'message': CAUGHT_UP_MESSAGES[kind],
                'img_url': FOX_IMAGE,
                'big': kind == 'all',
            },
        )

    # ------------------------------------------------------------------
    # Envois à temps
    # ------------------------------------------------------------------

    @api.model
    def _bf_credited_user(self, organizer=None):
        """À qui va l'XP d'un envoi : la personne qui agit si c'est un humain
        qui joue, sinon l'organisateur. Avant la 18.0.3.0.0, le créateur du CR : le
        robot des rencontres recevait presque tous les crédits « CR envoyé »."""
        actor = self.env.user
        if self._bf_is_player(actor):
            return actor
        if organizer and self._bf_is_player(organizer):
            return organizer
        return self.env['res.users']

    # ------------------------------------------------------------------
    # Congés de constance
    # ------------------------------------------------------------------

    @api.model
    def _bf_leave_settings(self):
        ICP = self.env['ir.config_parameter'].sudo()

        def number(key, default):
            try:
                return max(float(ICP.get_param(key, default)), 0)
            except ValueError:
                return float(default)

        return {
            'enabled': ICP.get_param('bf_gamification.leave_enabled', 'False') == 'True',
            'step': int(number('bf_gamification.leave_streak_days', '60')),
            'days_per_step': number('bf_gamification.leave_days_per_step', '1'),
            'max_per_year': int(number('bf_gamification.leave_max_per_year', '5')),
        }

    def _bf_leave_sync(self, run):
        """La marche vers le prochain congé, dérivée de la constance en cours.

        `run` : les jours ouvrables de la constance en cours. Comptent ceux
        d'après l'activation et d'après le dernier palier atteint : une
        constance cassée remet la marche à zéro, une constance réparée (absence
        déclarée après coup) la rend, et un palier déjà atteint ne se recompte
        jamais.
        """
        self.ensure_one()
        profile = self.sudo()
        if not profile.leave_opt_in:
            progress = 0
        else:
            settings = self._bf_leave_settings()
            if not settings['enabled'] or settings['step'] <= 0:
                return
            user = profile.user_id
            start = (fq_days.local_date(user, profile.leave_opt_in_date)
                     or fq_days.user_today(user))
            if profile.leave_last_step_day:
                start = max(start, profile.leave_last_step_day + timedelta(days=1))
            counted = [day for day in run if day >= start]
            step = settings['step']
            # Une seule demande par passage : abaisser le palier
            # de 60 à 3 ouvrait d'un coup une demande par tranche de 3 jours. Le
            # reste attend le passage suivant (prochain geste, cron du lendemain).
            if len(counted) >= step:
                profile.leave_last_step_day = counted[step - 1]
                profile._bf_open_leave_claim(settings)
                counted = counted[step:]
            progress = len(counted)
        if profile.leave_progress != progress:
            profile.leave_progress = progress

    def _bf_leave_claims_this_year(self, reward):
        self.ensure_one()
        user = self.user_id
        year_start = date(fq_days.user_today(user).year, 1, 1)
        return self.env['bf.gamification.reward.claim'].sudo().search_count([
            ('user_id', '=', user.id),
            ('reward_id', '=', reward.id),
            ('state', '!=', 'refused'),
            ('date_claimed', '>=', fq_days.local_midnight_utc(user, year_start)),
        ])

    def _bf_open_leave_claim(self, settings):
        self.ensure_one()
        reward = self.env.ref('bf_gamification.reward_leave_constance', raise_if_not_found=False)
        if not reward:
            return False
        cap = settings['max_per_year']
        if cap and self._bf_leave_claims_this_year(reward) >= cap:
            _logger.info("Fox Quest : palier de congé atteint par %s, plafond annuel (%d) déjà atteint",
                         self.user_id.login, cap)
            return False
        # La demande naît dans la requête de qui a déclenché le recalcul (un
        # collègue qui pose une absence, l'approbateur d'un congé, un cron) : ni
        # abonné (seules la bénéficiaire et l'approbatrice la suivent), ni auteur.
        # Création, note et activité sont signées par le système.
        # Et sans le contexte de l'appelant : un `default_date_claimed`
        # ancien la sortait du plafond annuel, un `default_approved_by` la posait
        # déjà décidée.
        claim = self.env['bf.gamification.reward.claim'].with_user(SUPERUSER_ID).with_context(
            clean_context(self.env.context),
            bf_gamification_constance=True, mail_create_nosubscribe=True).create({
                'user_id': self.user_id.id,
                'reward_id': reward.id,
                'leave_days': settings['days_per_step'],
            })
        claim._bf_ask_approval()
        return claim

    def _bf_set_leave_opt_in(self, flag):
        """La participation se pose par la personne elle-même, et par elle seule."""
        self.ensure_one()
        if self.user_id != self.env.user:
            raise UserError("Seule la personne concernée peut choisir de participer.")
        # Sous usurpation, l'identité est celle de la personne, le choix serait celui
        # du support : contre l'arbitrage (« l'employé active lui-même »).
        self._bf_refuse_if_impersonated(
            "Vous voyez Symbifox sous le compte d'une autre personne (usurpation du "
            "support) : la participation aux congés de constance ne se choisit que par la "
            "personne elle-même, depuis son propre compte.")
        profile = self.sudo()
        now = fields.Datetime.now()
        if flag:
            if not self._bf_leave_settings()['enabled']:
                raise UserError("Les congés de constance ne sont pas activés dans votre société.")
            if not self._bf_is_player(self.user_id):
                raise UserError("Ce compte ne participe pas à Fox Quest.")
            if not profile.leave_opt_in:
                profile.write({'leave_opt_in': True, 'leave_opt_in_date': now,
                               'leave_progress': 0})
                profile._bf_recompute_constance()
        elif profile.leave_opt_in:
            profile.write({'leave_opt_in': False, 'leave_opt_out_date': now,
                           'leave_progress': 0})
        return True

    # ------------------------------------------------------------------
    # Une absence ou un congé change : la constance se refait
    # ------------------------------------------------------------------

    @api.model
    def _bf_constance_changed(self, users):
        """Refait la constance des personnes dont les jours ouvrables viennent de changer.

        Ne casse jamais le geste qui l'a provoqué (déclarer une absence, valider
        un congé) : chaque personne dans son point de sauvegarde. Appelée par les
        congés et fériés (`resource.calendar.leaves`) et, pour les absences
        déclarées, par le pont `bf_gamification_contact_absence` (il remplace
        l'enveloppe posée à l'exécution, que base_automation retirait).
        """
        if not users:
            return
        for profile in self.sudo().search([('user_id', 'in', users.ids)]):
            with fq_savepoint.isolated(self.env, "constance de %s" % profile.user_id.login):
                profile._bf_recompute_constance()
