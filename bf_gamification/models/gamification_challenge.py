"""Défis du jour : trois petits objectifs par jour ouvrable, et une carte à tampons.

Chaque jour ouvrable d'une personne, Fox Quest lui propose trois défis tirés de ce
qu'elle peut vraiment faire (fermer deux tâches, saisir une heure, traiter un
ticket...). Le premier paie le double. Chaque défi accompli donne un tampon ; la
carte de cinq tampons paie un bonus, puis repart à zéro.

Rien de ce module ne crée un nouveau moyen de gagner de l'XP. Un défi se lit dans
le journal d'XP, qui ne garde que les gestes de la personne elle-même, dans sa
propre requête : ni un cron, ni un robot, ni un collègue, ni le support sous
usurpation ne font avancer un défi. Les lignes du jour ne s'écrivent que par le code
du module, en sudo, et ne se lisent que par la personne.
"""
import random
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from . import fq_days, fq_savepoint

SLOTS = 3
STAMPS_PER_CARD = 5
FIRST_SLOT_FACTOR = 2
MAX_CHALLENGE_XP = 500
# Les sources d'XP qui comptent un geste de travail. Ni les messages (un défi ne se
# paie jamais en messages), ni ce que Fox Quest verse lui-même (badge, récompense,
# streak, défi), ni une écriture à la main.
CHALLENGE_SOURCES = [
    ('task', 'Tâche'),
    ('activity', 'Activité'),
    ('timesheet', 'Feuille de temps'),
    ('email_triage', 'Triage courriel'),
    ('meeting', 'Compte rendu'),
    ('agenda', 'Ordre du jour'),
    ('helpdesk', 'Ticket'),
    ('document', 'Document'),
    ('decision', 'Décision'),
    ('knowledge_item', 'Élément de matrice'),
    ('hosting', 'Hébergement'),
    ('resolution', 'Résolution corporative'),
    ('on_time', 'À temps'),
    ('caught_up', 'À jour'),
]
CHALLENGE_EXCLUDED_SOURCES = ('badge', 'reward', 'streak', 'challenge', 'manual', 'message')


class GamificationChallenge(models.Model):
    _name = 'bf.gamification.challenge'
    _description = 'Défi du jour Fox Quest'
    _order = 'sequence, id'

    name = fields.Char(string="Nom", required=True, translate=True)
    description = fields.Char(string="Description", translate=True)
    icon = fields.Char(string="Icône", default='fa-star',
                       help="Classe Font Awesome, par exemple fa-check-square-o.")
    source = fields.Selection(CHALLENGE_SOURCES, string="Geste compté", required=True,
                              help="Les gestes de la personne elle-même, dans sa journée, qui paient de "
                                   "l'XP de cette source. Les messages ne comptent jamais.")
    model_name = fields.Char(
        string="Modèle requis",
        help="Le modèle de la fiche que le geste modifie (par exemple project.task). Le défi n'est "
             "proposé qu'aux personnes qui ont le droit de l'écrire. Vide : aucun droit requis.")
    target = fields.Integer(string="Nombre de gestes", required=True, default=1,
                            help="Un geste = une fiche (une tâche, un ticket...). Les lignes d'XP sans fiche "
                                 "(heures, listes à jour) comptent chacune pour un geste : gardez 1 pour ces sources.")
    xp_reward = fields.Integer(string="XP accordé", required=True, default=10,
                               help="Le premier défi de la journée paie le double.")
    sequence = fields.Integer(string="Ordre", default=10)
    active = fields.Boolean(string="Actif", default=True)

    @api.constrains('target', 'xp_reward')
    def _check_values(self):
        for challenge in self:
            if challenge.target < 1:
                raise ValidationError(_("Un défi demande au moins un geste."))
            if not 0 <= challenge.xp_reward <= MAX_CHALLENGE_XP:
                raise ValidationError(_("L'XP d'un défi va de 0 à %d.") % MAX_CHALLENGE_XP)

    @api.constrains('model_name')
    def _check_model_name(self):
        for challenge in self:
            if challenge.model_name and challenge.model_name not in self.env:
                raise ValidationError(_("Le modèle « %s » n'existe pas.") % challenge.model_name)

    def unlink(self):
        # Un défi déjà proposé garde son historique : on l'archive, on ne le supprime pas.
        if self.env['bf.gamification.challenge.line'].sudo().search_count(
                [('challenge_id', 'in', self.ids)]):
            raise UserError(_("Ce défi a déjà été proposé : archivez-le plutôt que de le supprimer."))
        return super().unlink()


class GamificationChallengeLine(models.Model):
    _name = 'bf.gamification.challenge.line'
    _description = "Défi du jour d'une personne"
    _order = 'day desc, slot'

    user_id = fields.Many2one('res.users', string="Utilisateur", required=True,
                              ondelete='cascade', index=True)
    day = fields.Date(string="Jour", required=True, index=True,
                      help="Le jour de la personne, dans son fuseau.")
    slot = fields.Integer(string="Emplacement", required=True)
    challenge_id = fields.Many2one('bf.gamification.challenge', string="Défi", required=True,
                                   ondelete='restrict')
    # Copiés le jour de l'émission : modifier le catalogue ne bouge pas une journée
    # déjà commencée.
    source = fields.Selection(CHALLENGE_SOURCES, string="Geste compté", required=True)
    target = fields.Integer(string="Nombre de gestes", required=True)
    xp_reward = fields.Integer(string="XP accordé", required=True)
    done = fields.Boolean(string="Accompli", default=False)
    done_date = fields.Datetime(string="Accompli le")

    @api.depends('challenge_id.name')
    def _compute_display_name(self):
        for line in self:
            line.display_name = line.challenge_id.name or _("Défi du jour")

    _sql_constraints = [
        ('one_per_slot', 'UNIQUE(user_id, day, slot)',
         "Un seul défi par emplacement et par jour."),
        ('one_per_challenge', 'UNIQUE(user_id, day, challenge_id)',
         "Un défi ne se propose qu'une fois par jour."),
    ]

    # Seul le code de Fox Quest, en sudo, écrit une ligne : ni la personne, ni un
    # gestionnaire ne se donnent un défi accompli.
    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            raise AccessError(_("Les défis du jour s'émettent seuls."))
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su:
            raise AccessError(_("Un défi du jour ne se modifie pas à la main."))
        return super().write(vals)

    def unlink(self):
        if not self.env.su:
            raise AccessError(_("Un défi du jour ne se supprime pas à la main."))
        return super().unlink()


class GamificationProfileChallenge(models.Model):
    _inherit = 'bf.gamification.profile'

    # La carte à tampons se lit comme la marche vers le congé de constance : ni les
    # collègues ni le classement n'en voient rien. Le titre porté, lui, est fait pour
    # être vu (classement, profil).
    stamp_count = fields.Integer(
        string="Tampons de la carte", readonly=True,
        groups='bf_gamification.group_gamification_manager')
    cards_completed = fields.Integer(
        string="Cartes complètes", readonly=True,
        groups='bf_gamification.group_gamification_manager')
    worn_badge_id = fields.Many2one(
        'bf.gamification.badge', string="Titre porté", readonly=True, ondelete='set null',
        help="Le badge dont la personne porte le nom comme titre.")

    # ------------------------------------------------------------------
    # Émission
    # ------------------------------------------------------------------

    @api.model
    def _bf_challenges_enabled(self):
        ICP = self.env['ir.config_parameter'].sudo()
        return (ICP.get_param('bf_gamification.gamification_enabled', 'True') == 'True'
                and ICP.get_param('bf_gamification.challenges_enabled', 'True') == 'True')

    @api.model
    def _bf_challenge_pool(self, user):
        """Les défis que la personne peut vraiment faire : actifs, dont la source paie de
        l'XP (une règle active et non nulle : sans elle, le défi ne pourrait jamais avancer),
        et dont elle a le droit d'écrire la fiche."""
        pool = self.env['bf.gamification.challenge'].sudo().search(
            [('active', '=', True)], order='sequence, id')
        paying = set(self.env['bf.gamification.xp.rule'].sudo().search(
            [('active', '=', True), ('xp_amount', '>', 0)]).mapped('source'))
        able = pool.browse()
        for challenge in pool:
            if challenge.source not in paying:
                continue
            model = challenge.model_name
            if model and (model not in self.env
                          or not self.env[model].with_user(user).has_access('write')):
                continue
            able |= challenge
        return able

    @api.model
    def _bf_ensure_challenges(self, user, day=None):
        """Les défis du jour de la personne, émis au premier appel de la journée.

        Un jour ouvrable seulement : le week-end, un férié ou une absence déclarée
        sont un vrai repos. Les trois défis sont tirés au hasard, avec pour graine
        la personne et le jour : le même tirage partout, sans rien garder d'autre
        que les lignes. Insertion qui ignore le conflit : la barre du haut et le
        tableau de bord appellent en même temps, et le perdant retrouve les lignes
        du gagnant.
        """
        day = day or fq_days.user_today(user)
        Line = self.env['bf.gamification.challenge.line'].sudo()
        lines = Line.search([('user_id', '=', user.id), ('day', '=', day)], order='slot')
        if lines or not self._bf_challenges_enabled() or not self._bf_is_player(user):
            return lines
        if not fq_days.is_working_day(self.env, user, day):
            return lines
        pool = self._bf_challenge_pool(user)
        if not pool:
            return lines
        previous_day = Line.search([('user_id', '=', user.id), ('day', '<', day)],
                                   order='day desc', limit=1).day
        yesterday = Line.search([('user_id', '=', user.id), ('day', '=', previous_day)]).challenge_id \
            if previous_day else pool.browse()
        # Ne pas reproposer ceux de la veille, tant qu'il en reste assez d'autres.
        fresh = pool - yesterday
        candidates = fresh if len(fresh) >= SLOTS else pool
        chosen = random.Random('%s:%s' % (user.id, day.isoformat())).sample(
            list(candidates), min(SLOTS, len(candidates)))
        self.env.flush_all()
        cr = self.env.cr
        for slot, challenge in enumerate(chosen, start=1):
            reward = challenge.xp_reward * (FIRST_SLOT_FACTOR if slot == 1 else 1)
            cr.execute("""
                INSERT INTO bf_gamification_challenge_line
                       (user_id, day, slot, challenge_id, source, target, xp_reward, done,
                        create_uid, create_date, write_uid, write_date)
                VALUES (%s, %s, %s, %s, %s, %s, %s, FALSE,
                        %s, now() AT TIME ZONE 'UTC', %s, now() AT TIME ZONE 'UTC')
                ON CONFLICT DO NOTHING
            """, (user.id, day, slot, challenge.id, challenge.source, challenge.target, reward,
                  self.env.uid, self.env.uid))
        Line.invalidate_model()
        return Line.search([('user_id', '=', user.id), ('day', '=', day)], order='slot')

    # ------------------------------------------------------------------
    # Progression
    # ------------------------------------------------------------------

    @api.model
    def _bf_challenge_counts(self, user, day, sources):
        """Combien de gestes de la personne, dans son jour, par source.

        Un geste : une référence distincte (fermer une tâche verse deux lignes,
        « complétée » et « avant l'échéance »). Une ligne sans référence compte pour un
        geste.
        """
        if not sources:
            return {}
        rows = self.env['bf.gamification.xp.transaction'].sudo().search_read([
            ('user_id', '=', user.id),
            ('source', 'in', list(sources)),
            ('from_request', '=', True),
            ('xp_amount', '>', 0),
            ('date', '>=', fq_days.local_midnight_utc(user, day)),
            ('date', '<', fq_days.local_midnight_utc(user, day + timedelta(days=1))),
        ], ['source', 'reference'])
        gestures = {}
        for row in rows:
            gestures.setdefault(row['source'], set()).add(row['reference'] or ('ligne', row['id']))
        return {source: len(keys) for source, keys in gestures.items()}

    def _bf_progress_challenges(self, txn):
        """Appelé après chaque crédit d'XP : le geste vient-il d'accomplir un défi ?

        Rien ne se passe pour ce qui n'est pas un geste de la personne elle-même
        (le journal le sait déjà : `from_request`), ni pour l'XP que Fox Quest verse
        lui-même. Ne casse jamais le geste qui l'a provoqué.
        """
        self.ensure_one()
        if (not txn.from_request or txn.xp_amount <= 0
                or txn.source in CHALLENGE_EXCLUDED_SOURCES
                or not self._bf_challenges_enabled()):
            return
        user = self.user_id
        completed = []
        with fq_savepoint.isolated(self.env, "défis du jour de %s" % user.login) as outcome:
            day = fq_days.user_today(user)
            lines = self._bf_ensure_challenges(user, day).filtered(
                lambda line: not line.done and line.source == txn.source)
            if lines:
                count = self._bf_challenge_counts(user, day, {txn.source}).get(txn.source, 0)
                for line in lines:
                    if count >= line.target:
                        completed.append((line, self._bf_complete_challenge(line)))
        # Le message de bus ne se retire pas avec un point de sauvegarde : il ne part que si
        # le bloc est allé au bout, point de sauvegarde fermé compris.
        if outcome.ok:
            for line, card_complete in completed:
                if card_complete is not None:
                    self._bf_notify_challenge(line, card_complete)

    def _bf_complete_challenge(self, line):
        """Marque le défi accompli, paie, tamponne. Une seule fois, même si deux
        requêtes arrivent ensemble : la marque est posée par un UPDATE conditionnel.

        Rend `None` si une autre requête l'a déjà accompli, sinon si la carte vient d'être
        complétée (la notification, elle, part plus tard, quand tout le bloc a réussi)."""
        self.ensure_one()
        cr = self.env.cr
        self.env.flush_all()
        cr.execute("""
            UPDATE bf_gamification_challenge_line
               SET done = TRUE, done_date = now() AT TIME ZONE 'UTC',
                   write_uid = %s, write_date = now() AT TIME ZONE 'UTC'
             WHERE id = %s AND done IS NOT TRUE
         RETURNING id
        """, (self.env.uid, line.id))
        if not cr.fetchone():
            return None
        line.invalidate_recordset(['done', 'done_date'])
        if line.xp_reward:
            # Le journal est lisible des gestionnaires, qui ne voient pas les journées « à jour »
            # d'une personne : un défi de ce genre ne nomme pas ce qu'il récompense.
            label = (_("Défi du jour : %s") % line.challenge_id.name if line.source != 'caught_up'
                     else _("Défi du jour accompli"))
            self._award_xp(line.xp_reward, 'challenge', label, reference=line)
        return self._bf_add_stamp()

    def _bf_add_stamp(self):
        """Un tampon de plus. Au dernier, la carte paie son bonus et repart à zéro."""
        self.ensure_one()
        profile = self.sudo()
        stamps = profile.stamp_count + 1
        if stamps < STAMPS_PER_CARD:
            profile.write({'stamp_count': stamps})
            return False
        profile.write({'stamp_count': 0, 'cards_completed': profile.cards_completed + 1})
        self._bf_award_rule('bf_gamification.xp_rule_challenge_card', 'challenge',
                            _("Carte de défis complète"))
        return True

    def _bf_notify_challenge(self, line, card_complete):
        if self.env['ir.config_parameter'].sudo().get_param(
                'bf_gamification.popup_enabled', 'True') != 'True':
            return
        done_messages = (
            _("Défi accompli ! Le renard est fier de vous."),
            _("Un de moins, et un tampon de plus."),
            _("Bien joué : le renard remue la queue."),
        )
        self.env['bus.bus']._sendone(
            self.user_id.partner_id,
            'bf_gamification/challenge_done',
            {
                'name': line.challenge_id.name,
                'xp': line.xp_reward,
                'stamps': self.sudo().stamp_count,
                'per_card': STAMPS_PER_CARD,
                'card_complete': card_complete,
                'message': (_("Carte de défis complète ! Le renard vous doit une fête.")
                            if card_complete else done_messages[line.id % len(done_messages)]),
                'img_url': '/bf_gamification/static/img/renard_a_jour.svg',
            },
        )

    # ------------------------------------------------------------------
    # Ce que voit la personne
    # ------------------------------------------------------------------

    @api.model
    def _bf_challenges_data(self, user):
        """Les défis du jour de la personne, pour le tableau de bord."""
        profile = self._get_or_create_profile(user).sudo()
        data = {
            'enabled': self._bf_challenges_enabled(),
            'per_card': STAMPS_PER_CARD,
            'stamps': profile.stamp_count,
            'cards_completed': profile.cards_completed,
            'lines': [],
            'rest_day': False,
            'all_done': False,
        }
        if not data['enabled']:
            return data
        day = fq_days.user_today(user)
        lines = self._bf_ensure_challenges(user, day)
        if not lines:
            # Les lignes manquent : soit un jour de repos, soit rien à proposer. Seul le premier
            # se dit à la personne.
            data['rest_day'] = not fq_days.is_working_day(self.env, user, day)
            return data
        counts = self._bf_challenge_counts(user, day, {line.source for line in lines if not line.done})
        for line in lines:
            progress = line.target if line.done else min(counts.get(line.source, 0), line.target)
            data['lines'].append({
                'id': line.id,
                'slot': line.slot,
                'name': line.challenge_id.name,
                'description': line.challenge_id.description or '',
                'icon': line.challenge_id.icon or 'fa-star',
                'target': line.target,
                'progress': progress,
                'xp': line.xp_reward,
                'done': line.done,
                'bonus': line.slot == 1,
            })
        data['all_done'] = all(line.done for line in lines)
        return data

    # ------------------------------------------------------------------
    # Titres
    # ------------------------------------------------------------------

    def _bf_set_worn_title(self, badge_id):
        """Porte, ou retire (`False`), le nom d'un badge que la personne a obtenu."""
        self.ensure_one()
        profile = self.sudo()
        if not badge_id:
            profile.write({'worn_badge_id': False})
            return
        if not self.env['bf.gamification.user.badge'].sudo().search_count([
                ('user_id', '=', profile.user_id.id), ('badge_id', '=', badge_id)]):
            raise UserError(_("Ce badge n'est pas encore à vous."))
        profile.write({'worn_badge_id': badge_id})
