# -*- coding: utf-8 -*-
"""Le chronomètre vit sur l'ordre du jour : c'est lui qui porte la liste des
sujets, leurs minutes allouées et l'heure de fin prévue.

Trois règles ont dicté ce modèle, et chacune vient d'un piège mesuré :

* **L'origine du temps est le geste « Démarrer », jamais une date déjà en base.**
  La date d'un compte rendu ne porte pas l'heure réelle de départ : elle est
  souvent posée à l'heure ronde, ou dans un autre fuseau.
* **Le serveur porte l'horloge, l'écran ne fait que dessiner.** Le panneau reçoit
  l'instant du serveur avec chaque état et corrige le décalage de l'horloge du
  navigateur, qui ment et dont l'onglet s'endort.
* **Un sujet repris plus tard cumule ses passages.** Un segment de rencontre
  n'est pas un intervalle, c'est une somme d'intervalles : c'est la différence
  de fond avec un chronomètre de speedrun, et ce qui interdit d'en reprendre le
  format.
"""

import logging

from markupsafe import Markup

from odoo import _, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class MeetingAgenda(models.Model):
    _inherit = 'meeting.agenda'

    # Tout est copy=False : cloner un ordre du jour depuis la série ne doit
    # jamais emporter le temps d'une autre rencontre.
    timer_state = fields.Selection(
        [
            ('idle', 'Non démarré'),
            ('running', 'En cours'),
            ('paused', 'En pause'),
            ('done', 'Terminé'),
        ],
        string='Chronomètre',
        default='idle',
        required=True,
        copy=False,
        index=True,
    )
    timer_started_at = fields.Datetime(
        string='Départ du chronomètre',
        copy=False,
        help="Le moment où on a pressé Démarrer, pas la date prévue de la rencontre.",
    )
    timer_ended_at = fields.Datetime(string='Fin du chronomètre', copy=False)
    timer_elapsed_seconds = fields.Integer(
        string='Écoulé (secondes)',
        default=0,
        copy=False,
        help="Temps couru hors pauses, versé à chaque transition.",
    )
    timer_segment_since = fields.Datetime(
        string='Tranche en cours depuis',
        copy=False,
        help="Début de la tranche qui n'est pas encore versée. Vide dès que le "
             "chronomètre n'avance pas.",
    )
    timer_current_topic_id = fields.Many2one(
        'meeting.agenda.topic',
        string='Sujet en cours',
        copy=False,
        ondelete='set null',
    )
    timer_panel_placement = fields.Selection(
        related='company_id.meeting_timer_placement',
        string='Place du chronomètre',
        readonly=True,
        help="Sert uniquement à la vue, qui décide où poser le panneau. Le repli "
             "vaut « au-dessus des onglets » : une société dont la colonne est "
             "restée nulle n'a pas de panneau qui disparaît.",
    )

    # ------------------------------------------------------------------
    # Le départ de la rencontre emporte le départ du chronomètre
    # ------------------------------------------------------------------
    def action_start_meeting(self):
        """« Démarrer la rencontre » lance aussi le chronomètre.

        C'est le geste qu'on fait déjà, et celui qu'on croit avoir fait :
        presser ce bouton sans que le chronomètre parte laisse une rencontre
        démarrée et un chronomètre à zéro.

        🔴 Sous filet. Le chronomètre est un ajout à ce bouton, pas sa raison
        d'être : s'il refuse de partir, la rencontre démarre quand même. Le
        contraire ferait tomber une fonction qui marchait avant lui, exactement
        comme le récapitulatif au chatter empêchait de terminer.
        """
        resultat = super().action_start_meeting()
        for rec in self:
            if rec.timer_state != 'idle' or rec.state == 'cancelled':
                continue
            try:
                rec.action_timer_start()
            except Exception:
                _logger.warning(
                    "Chronomètre : départ automatique refusé sur l'ordre du jour "
                    "%s. La rencontre, elle, est bien démarrée.",
                    rec.id, exc_info=True)
        return resultat

    # ------------------------------------------------------------------
    # Gardes
    # ------------------------------------------------------------------
    def _timer_guard(self, operation='write'):
        """Une méthode publique est appelable par XML-RPC : chacune vérifie.

        `check_access` lève une `AccessError` si la personne n'a pas le droit
        demandé sur CET enregistrement (les règles de `bf_meeting` bornent déjà
        les ordres du jour aux membres du projet).
        """
        self.ensure_one()
        self.check_access(operation)
        # 🔴 Les gestes écrivent AUSSI sur les sujets. Sans cette seconde
        # vérification, un droit d'écriture sur l'ordre du jour mais pas sur ses
        # sujets laisse un chronomètre À MOITIÉ PARTI : l'ordre du jour est
        # écrit, le sujet refusé. L'ORM refuserait bien l'écriture du sujet,
        # mais trop tard.
        if operation != 'read' and self.topic_ids:
            self.topic_ids.check_access(operation)
        return self

    def _timer_now(self):
        return fields.Datetime.now()

    def _timer_topics(self):
        """Les sujets qui participent à la rencontre, dans l'ordre de l'ordre du jour.

        Un sujet proposé par un destinataire et pas encore accepté n'entre ni
        dans le PDF ni dans le courriel : il n'entre pas non plus dans la course.
        """
        self.ensure_one()
        topics = self.topic_ids.filtered(lambda t: t.moderation_state == 'accepted')
        return topics.sorted(lambda t: (t.sequence, t.id))

    # ------------------------------------------------------------------
    # Mécanique interne
    # ------------------------------------------------------------------
    def _timer_close_slice(self, now=None):
        """Verser la tranche courue dans le total et dans le sujet en cours."""
        self.ensure_one()
        if self.timer_state != 'running' or not self.timer_segment_since:
            return 0
        now = now or self._timer_now()
        seconds = int((now - self.timer_segment_since).total_seconds())
        if seconds < 0:
            seconds = 0
        vals = {'timer_elapsed_seconds': self.timer_elapsed_seconds + seconds,
                'timer_segment_since': False}
        topic = self.timer_current_topic_id
        if topic:
            topic.timer_seconds = topic.timer_seconds + seconds
        self.write(vals)
        return seconds

    def _timer_open_topic(self, topic, now=None):
        """Ouvrir un sujet : il devient le sujet en cours et compte un passage."""
        self.ensure_one()
        now = now or self._timer_now()
        # 🔴 C'est ICI et nulle part ailleurs que la tranche repart : trois
        # `self.timer_segment_since = now` traînaient dans les gestes, juste
        # avant cet appel, et ne servaient à rien puisque le `write` ci-dessous
        # les réécrit. Une mutation qui survit sans `assertRaises` accuse
        # d'abord une ligne morte : c'était le cas.
        if topic:
            topic.write({
                'timer_state': 'current',
                'timer_visits': topic.timer_visits + 1,
                'timer_first_at': topic.timer_first_at or now,
            })
        self.write({
            'timer_current_topic_id': topic.id if topic else False,
            'timer_segment_since': now if self.timer_state == 'running' else False,
        })

    def _timer_next_topic(self):
        """Le prochain sujet pas encore abordé, dans l'ordre de l'ordre du jour."""
        self.ensure_one()
        current = self.timer_current_topic_id
        for topic in self._timer_topics():
            if topic == current:
                continue
            if topic.timer_state == 'pending':
                return topic
        return self.env['meeting.agenda.topic']

    # ------------------------------------------------------------------
    # Gestes
    # ------------------------------------------------------------------
    def action_timer_start(self):
        self._timer_guard()
        if self.timer_state != 'idle':
            raise UserError(_("Le chronomètre est déjà parti."))
        if self.state == 'cancelled':
            raise UserError(_("Cette rencontre est annulée."))
        now = self._timer_now()
        self.write({
            'timer_state': 'running',
            'timer_started_at': now,
            'timer_ended_at': False,
            'timer_elapsed_seconds': 0,
            'timer_segment_since': now,
        })
        first = self._timer_next_topic()
        if first:
            self._timer_open_topic(first, now)
        return self.timer_payload()

    def action_timer_split(self):
        """Sujet suivant : le sujet en cours est fait, le prochain commence."""
        self._timer_guard()
        if self.timer_state != 'running':
            raise UserError(_("Le chronomètre n'avance pas."))
        now = self._timer_now()
        self._timer_close_slice(now)
        if self.timer_current_topic_id:
            self.timer_current_topic_id.timer_state = 'done'
        nxt = self._timer_next_topic()
        if not nxt:
            return self._timer_finish(now)
        self._timer_open_topic(nxt, now)
        return self.timer_payload()

    def action_timer_goto(self, topic_id):
        """Revenir sur un sujet, ou en ouvrir un hors d'ordre. Il cumule ses passages.

        Permis aussi **en pause** : on met la rencontre en pause, puis on décide
        de reprendre un sujet. Rien ne court pendant ce temps, la tranche ne
        repart qu'à la reprise.
        """
        self._timer_guard()
        if self.timer_state not in ('running', 'paused'):
            raise UserError(_("Le chronomètre n'est pas parti."))
        topic = self.env['meeting.agenda.topic'].browse(int(topic_id)).exists()
        if not topic or topic.agenda_id != self:
            raise UserError(_("Ce sujet n'appartient pas à cet ordre du jour."))
        if topic.moderation_state != 'accepted':
            raise UserError(_("Ce sujet n'est pas encore accepté."))
        now = self._timer_now()
        self._timer_close_slice(now)
        previous = self.timer_current_topic_id
        if previous and previous != topic and previous.timer_state == 'current':
            previous.timer_state = 'done'
        # Le sujet s'ouvre même en pause : c'est `_timer_open_topic` qui refuse
        # de faire repartir la tranche tant que le chronomètre n'avance pas.
        self._timer_open_topic(topic, now)
        return self.timer_payload()

    def action_timer_skip(self):
        """Passer le sujet en cours : il est marqué sauté, le temps déjà couru reste.

        Le temps couru est réel, on ne l'efface pas. Sauter dit « on ne l'a pas
        couvert », pas « personne n'a passé de temps dessus ».
        """
        self._timer_guard()
        if self.timer_state != 'running':
            raise UserError(_("Le chronomètre n'avance pas."))
        now = self._timer_now()
        self._timer_close_slice(now)
        if self.timer_current_topic_id:
            self.timer_current_topic_id.timer_state = 'skipped'
        nxt = self._timer_next_topic()
        if not nxt:
            return self._timer_finish(now)
        self._timer_open_topic(nxt, now)
        return self.timer_payload()

    def action_timer_pause(self):
        self._timer_guard()
        if self.timer_state != 'running':
            raise UserError(_("Le chronomètre n'avance pas."))
        self._timer_close_slice()
        self.timer_state = 'paused'
        return self.timer_payload()

    def action_timer_resume(self):
        self._timer_guard()
        if self.timer_state != 'paused':
            raise UserError(_("Le chronomètre n'est pas en pause."))
        self.write({
            'timer_state': 'running',
            'timer_segment_since': self._timer_now(),
        })
        return self.timer_payload()

    def action_timer_stop(self):
        self._timer_guard()
        if self.timer_state not in ('running', 'paused'):
            raise UserError(_("Le chronomètre n'est pas parti."))
        return self._timer_finish()

    def _timer_finish(self, now=None):
        self.ensure_one()
        now = now or self._timer_now()
        self._timer_close_slice(now)
        if self.timer_current_topic_id and self.timer_current_topic_id.timer_state == 'current':
            self.timer_current_topic_id.timer_state = 'done'
        self.write({
            'timer_state': 'done',
            'timer_ended_at': now,
            'timer_segment_since': False,
            'timer_current_topic_id': False,
        })
        self._timer_post_recap()
        return self.timer_payload()

    def action_timer_reset(self):
        """Effacer la course. Ce n'est pas le « reset » d'un speedrun (il n'y a pas
        de tentative suivante), c'est la sortie d'un départ pressé par erreur."""
        self._timer_guard()
        self._timer_topics().write({
            'timer_seconds': 0,
            'timer_visits': 0,
            'timer_state': 'pending',
            'timer_first_at': False,
        })
        self.write({
            'timer_state': 'idle',
            'timer_started_at': False,
            'timer_ended_at': False,
            'timer_elapsed_seconds': 0,
            'timer_segment_since': False,
            'timer_current_topic_id': False,
        })
        return self.timer_payload()

    # ------------------------------------------------------------------
    # Lecture
    # ------------------------------------------------------------------
    def _timer_elapsed_at(self, now):
        """Écoulé total, tranche en cours comprise."""
        self.ensure_one()
        seconds = self.timer_elapsed_seconds
        if self.timer_state == 'running' and self.timer_segment_since:
            seconds += max(0, int((now - self.timer_segment_since).total_seconds()))
        return seconds

    def _timer_topic_seconds_at(self, topic, now):
        """Temps d'un sujet, tranche en cours comprise s'il est le sujet ouvert."""
        self.ensure_one()
        seconds = topic.timer_seconds
        if (self.timer_state == 'running' and self.timer_segment_since
                and topic == self.timer_current_topic_id):
            seconds += max(0, int((now - self.timer_segment_since).total_seconds()))
        return seconds

    def timer_payload(self):
        """L'état complet, tel que le panneau et le rapport le lisent.

        Le panneau ne calcule rien qu'il puisse calculer faux : les écarts et
        l'heure de fin projetée sont décidés ici, et `server_now` lui sert à
        corriger l'horloge de son navigateur.
        """
        self._timer_guard('read')
        now = self._timer_now()
        topics = self._timer_topics()
        current = self.timer_current_topic_id

        lignes = []
        ecart = 0           # écart cumulé sur ce qui est couvert, en secondes
        reste_alloue = 0    # minutes allouées à ce qui n'est pas couvert
        for topic in topics:
            seconds = self._timer_topic_seconds_at(topic, now)
            prevu = (topic.duration_planned or 0) * 60
            if topic.timer_state in ('done', 'skipped') or topic == current:
                ecart += seconds - prevu
            else:
                reste_alloue += prevu
            lignes.append({
                'id': topic.id,
                'name': topic.name or '',
                'sequence': topic.sequence,
                'planned_minutes': topic.duration_planned or 0,
                'seconds': seconds,
                'state': topic.timer_state,
                'visits': topic.timer_visits,
                'is_current': bool(current and topic == current),
                'delta_seconds': seconds - prevu if (
                    topic.timer_state in ('done', 'skipped') or topic == current) else 0,
            })

        # Le reste du sujet ouvert : ce qu'il lui reste d'alloué, jamais négatif.
        reste_courant = 0
        if current:
            prevu = (current.duration_planned or 0) * 60
            reste_courant = max(0, prevu - self._timer_topic_seconds_at(current, now))

        # Heure de fin projetée : maintenant, plus ce qui reste d'alloué au sujet
        # ouvert, plus l'alloué des sujets pas encore abordés. C'est la « current
        # pace » d'un chronomètre de course, appliquée au plan.
        projetee = None
        if self.timer_state in ('running', 'paused'):
            projetee = fields.Datetime.add(now, seconds=reste_courant + reste_alloue)
        elif self.timer_state == 'done':
            projetee = self.timer_ended_at

        prevue = None
        if self.timer_started_at and self.duration_planned:
            prevue = fields.Datetime.add(self.timer_started_at, minutes=self.duration_planned)

        return {
            'id': self.id,
            'state': self.timer_state,
            'agenda_state': self.state,
            'server_now': fields.Datetime.to_string(now),
            'started_at': fields.Datetime.to_string(self.timer_started_at) if self.timer_started_at else False,
            'ended_at': fields.Datetime.to_string(self.timer_ended_at) if self.timer_ended_at else False,
            'elapsed_seconds': self._timer_elapsed_at(now),
            'planned_minutes': self.duration_planned or 0,
            'planned_end': fields.Datetime.to_string(prevue) if prevue else False,
            'projected_end': fields.Datetime.to_string(projetee) if projetee else False,
            'delta_seconds': ecart,
            'remaining_planned_seconds': reste_courant + reste_alloue,
            'current_topic_id': current.id if current else False,
            'topics': lignes,
        }

    # ------------------------------------------------------------------
    # Trace
    # ------------------------------------------------------------------
    def _timer_recap_lines(self):
        """Les lignes du récapitulatif, partagées par le chatter et le rapport.

        Les sujets JAMAIS ouverts y figurent aussi : « on n'a pas eu le temps de
        parler des prochaines étapes » est précisément ce qu'un compte rendu doit
        dire. Les taire donnerait une liste qui se lit comme un ordre du jour
        tenu au complet.
        """
        self.ensure_one()
        now = self._timer_now()
        lignes = []
        for topic in self._timer_topics():
            seconds = self._timer_topic_seconds_at(topic, now)
            lignes.append({
                'name': topic.name or '',
                'planned_minutes': topic.duration_planned or 0,
                'minutes': round(seconds / 60.0, 1),
                'seconds': seconds,
                'state': topic.timer_state,
                'visits': topic.timer_visits,
                'covered': bool(topic.timer_visits),
            })
        return lignes

    def _timer_post_recap(self):
        """Une note au chatter à la fin de la course, pas une par geste.

        🔴 Le nom d'un sujet est saisi par un humain et peut contenir du HTML :
        il est échappé un à un. Concaténer du `Markup` avec du `str` échappe le
        `str`, c'est la seule façon sûre de bâtir ce corps.
        """
        self.ensure_one()
        lignes = self._timer_recap_lines()
        if not lignes:
            return
        total = round(self.timer_elapsed_seconds / 60.0)
        prevu = self.duration_planned or 0
        corps = Markup('<p>%s</p>') % _(
            "Chronomètre : %(total)s min courues, %(prevu)s min prévues.",
            total=total, prevu=prevu)
        items = Markup('')
        for ligne in lignes:
            if not ligne['covered']:
                mention = _(" (non abordé)")
            elif ligne['state'] == 'skipped':
                mention = _(" (sauté)")
            elif ligne['visits'] > 1:
                mention = _(" (%s passages)", ligne['visits'])
            else:
                mention = ''
            items += Markup('<li>%s</li>') % _(
                "%(nom)s : %(reel)s min pour %(prevu)s prévues%(mention)s",
                nom=ligne['name'], reel=ligne['minutes'],
                prevu=ligne['planned_minutes'], mention=mention)
        # 🔴 La trace ne doit JAMAIS emporter la mesure. Un `message_post` peut
        # échouer pour une raison qui n'a rien à voir avec le chronomètre : un
        # usager sans adresse courriel fait lever « Unable to send message,
        # please configure the sender's email address ». L'exception remontait
        # jusqu'au
        # bouton « Terminer », qui refusait de terminer : la rencontre restait
        # en cours et le seul geste restant, « Effacer », aurait perdu tout le
        # temps mesuré.
        try:
            self.message_post(body=corps + Markup('<ul>%s</ul>') % items)
        except Exception:
            _logger.warning(
                "Chronomètre : récapitulatif non publié au chatter de l'ordre du "
                "jour %s. Les temps par sujet, eux, sont écrits.",
                self.id, exc_info=True)
