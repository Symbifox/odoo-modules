"""Mode « ne pas déranger » — un service consulté avant chaque avis à l'écran.

CE QU'IL FAIT TAIRE, ET CE QU'IL NE FAIT PAS
--------------------------------------------
Deux sources, décidées le 09-01 : la popup d'arrivée de courriel
(``popup_transport.py``) et le rappel d'agenda
(``calendar_alarm_manager.do_check_alarm_for_one_date``). Le SMS entrant
(``bf_sms_archive``) et les badges (``bf_gamification``) restent dehors : ils
vivent dans d'autres modules, et le lot n'en touche qu'un.

L'avis n'est ni jeté ni poussé vers le téléphone. Il est RETENU, et un résumé
sort à la fin du mode. ``bf_email.push_enabled`` reste donc à 0 et rien ne
rouvre la question de la poussée vers le téléphone.

TROIS ENTRÉES QUI S'ADDITIONNENT
--------------------------------
1. l'armement automatique pendant les rencontres ;
2. l'interrupteur manuel, avec une durée, qui force dans les DEUX sens ;
3. les heures calmes.

⚠️ « Occupé » ne veut pas dire « en rencontre ». Mesuré sur trente jours d'un
agenda réel : ``show_as = busy`` seul vaut trois fois plus d'heures, parce
qu'il avale les plages qu'on se réserve pour soi. La règle retenue est
**occupé + plus d'un participant + hors journée entière**, et elle ne garde
que de vraies rencontres. Deux filtres qui paraissent évidents ont été écartés
à la mesure et ne doivent pas revenir :

- ``calendar.attendee.state = accepted`` : la grande majorité des lignes sont
  à ``needsAction``, les événements venus d'un agenda CalDAV n'apportant aucun
  RSVP. Ce filtre ramènerait le mode à une rencontre sur sept.
- ``videocall_location`` : présent sur moins de la moitié des rencontres.

🔴 LE GARDE SERVEUR NE SUFFIT PAS POUR L'AGENDA
-----------------------------------------------
``calendar.alarm_manager.get_next_notif`` rend les alarmes des **24 prochaines
heures** (``time_limit = 3600 * 24``) et le client arme chaque rappel avec un
``setTimeout(..., notif.timer * 1000)``. Mesuré : des poussées portant un
``timer`` de plus de 72 000 secondes, pour un rappel du lendemain
après-midi. Un mode qui s'arme au moment de la rencontre n'est jamais
consulté : le minuteur a été posé la veille.

D'où le canal ``bf_dnd/state``. À CHAQUE bascule, dans les deux sens, le
client rejoue ``/calendar/notify`` :

- à l'entrée, le sondage rend une liste vide et le client efface tous ses
  minuteurs. ⚠️ Une poussée bus vide ne suffirait pas :
  ``displayCalendarNotification`` retourne tôt sur ``if (!fresh.length)`` et
  garde l'horaire déjà armé. Seul le chemin du SONDAGE efface.
- à la sortie, le sondage rend de nouveau les alarmes et le client se réarme.
  ⚠️ C'est indispensable : après un sondage vide, ``lastNotifTimer`` vaut 0,
  donc le client ne reprogramme AUCUN sondage suivant et se tairait jusqu'au
  prochain rechargement de page.

⚠️ Rien n'est ancré sur un ``calendar.event.id`` : ``calendar_nextcloud_sync``
rase une série récurrente et la recrée avec des identifiants neufs. La file de
retenue se classe sous la clé de ``bf.calendar.reminder.ack``, l'UID CalDAV et
l'heure d'occurrence.

DÉFAUT ÉTEINT À L'INSTANCE
--------------------------
``bf_email.dnd_enabled`` est absent à l'installation, et une clé absente vaut
« non ». Le module est installé chez plusieurs locataires : un ``-u``
ne doit changer le comportement de personne.
"""

import logging
from datetime import timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

DND_STATE_CHANNEL = "bf_dnd/state"
DND_ENABLED_PARAM = "bf_email.dnd_enabled"
TRUTHY = ("1", "true", "yes", "on")

# Raisons d'être en mode, de la plus forte à la plus faible. L'ordre est celui
# de `_state_for` : un « laisse-moi tranquille » manuel prime sur l'agenda,
# et un « dérange-moi quand même » manuel prime sur tout.
REASONS = {
    "manual": "interrupteur manuel",
    "meeting": "rencontre en cours",
    "quiet": "heures calmes",
}

# Combien d'expéditeurs le résumé de sortie nomme avant de compter le reste.
DIGEST_NAMED = 3
# Identifiants joints au résumé pour que le client puisse nommer les
# expéditeurs. Ce ne sont QUE des identifiants : c'est lui qui relit les
# lignes par l'ORM, donc les règles d'enregistrement s'appliquent.
DIGEST_PREVIEW_IDS = 8
# Le plafond d'affichage du module, repris tel quel (voir popup_transport).
DIGEST_TTL_MS = 30000


class BfDnd(models.AbstractModel):
    """L'état « ne pas déranger » d'une personne, et rien d'autre.

    Un modèle abstrait plutôt qu'un enregistrement : l'état se CALCULE à
    chaque lecture, à partir des trois entrées. Le seul état stocké est le
    témoin de bascule (``res.users.bf_dnd_last_state``), qui sert au cron à
    reconnaître un changement, pas à décider.
    """

    _name = "bf.dnd"
    _description = "Mode « ne pas déranger »"

    # ------------------------------------------------------------------
    # Interrupteur d'instance
    # ------------------------------------------------------------------
    @api.model
    def _instance_enabled(self):
        """Vrai seulement si le paramètre d'instance dit oui.

        ⚠️ Aucun défaut passé à ``get_param`` : une clé absente rend ``False``
        et une clé vide rend ``""``, et les deux doivent valoir « non ».
        Même raisonnement que ``bf.email.popup._instance_enabled``.
        """
        value = self.env["ir.config_parameter"].sudo().get_param(
            DND_ENABLED_PARAM)
        return str(value or "").strip().lower() in TRUTHY

    # ------------------------------------------------------------------
    # L'état
    # ------------------------------------------------------------------
    @api.model
    def _state_for(self, user, now=None):
        """L'état courant de cette personne.

        Rend toujours un dictionnaire, jamais ``None`` :
        ``{"active", "reason", "until", "event"}``. ``until`` peut être faux
        quand la fin n'est pas connue d'avance.
        """
        blank = {"active": False, "reason": False, "until": False,
                 "event": self.env["calendar.event"].browse()}
        if not user or not self._instance_enabled():
            return blank
        # ⚠️ `sudo` sur la LECTURE des réglages. Les deux gardes s'exécutent
        # sous des identités variées — le cron de miroir IMAP tourne sous
        # OdooBot, `do_check_alarm_for_one_date` sous la personne elle-même —
        # et `res.users` refuse à un usager ordinaire de lire les champs d'un
        # AUTRE. Sans ça, faire taire quelqu'un dépendrait de qui synchronise
        # sa boîte, ce qui ne se verrait qu'en production.
        user = user.sudo()
        now = now or fields.Datetime.now()

        # Le « dérange-moi quand même » prime sur tout : c'est le geste par
        # lequel on reprend la main sur l'armement automatique.
        if user.bf_dnd_manual_off_until and user.bf_dnd_manual_off_until > now:
            return blank

        # ⚠️ « Indéfiniment » est un booléen, pas une date lointaine : une
        # échéance en 2099 se lit comme un réglage accidentel, et le résumé de
        # sortie n'arriverait jamais. `until` est alors faux, et l'écran doit
        # dire « jusqu'à ce que vous l'éteigniez » plutôt qu'une heure.
        if user.bf_dnd_manual_forever:
            return {"active": True, "reason": "manual", "until": False,
                    "event": self.env["calendar.event"].browse()}

        if user.bf_dnd_manual_until and user.bf_dnd_manual_until > now:
            return {"active": True, "reason": "manual",
                    "until": user.bf_dnd_manual_until,
                    "event": self.env["calendar.event"].browse()}

        if user.bf_dnd_meetings:
            event = self._meeting_now(user, now=now)
            if event:
                return {"active": True, "reason": "meeting",
                        "until": event.stop, "event": event}

        if user.bf_dnd_quiet_enabled:
            end = self._quiet_window_end(user, now=now)
            if end:
                return {"active": True, "reason": "quiet", "until": end,
                        "event": self.env["calendar.event"].browse()}

        return blank

    @api.model
    def _active_for(self, user, now=None):
        return self._state_for(user, now=now)["active"]

    # ------------------------------------------------------------------
    # L'agenda
    # ------------------------------------------------------------------
    @api.model
    def _meeting_now(self, user, now=None):
        """La rencontre en cours de cette personne, ou un ensemble vide.

        Occupé, plus d'un participant, hors journée entière. Voir l'en-tête du
        fichier pour ce que la mesure a écarté et pourquoi.

        ⚠️ ``sudo`` sur la recherche : le cron tourne sous OdooBot et les
        règles d'enregistrement de ``calendar.event`` lui cacheraient l'agenda
        de la personne. Le filtre sur son partenaire est ce qui borne la
        lecture, pas les droits du lecteur.
        """
        user = user.sudo()
        partner = user.partner_id
        if not partner:
            return self.env["calendar.event"].browse()
        now = now or fields.Datetime.now()
        events = self.env["calendar.event"].sudo().search([
            ("partner_ids", "in", partner.id),
            ("allday", "=", False),
            ("show_as", "=", "busy"),
            ("start", "<=", now),
            ("stop", ">", now),
        ], order="stop desc")
        # Plus d'un participant. Le filtre se fait en Python : un
        # `search_count` par événement coûterait une requête par ligne, et la
        # liste est courte par construction (les rencontres EN COURS).
        real = events.filtered(lambda e: len(e.partner_ids) > 1)
        # La plus tardive d'abord : deux rencontres qui se chevauchent doivent
        # laisser le mode armé jusqu'à la fin de la SECONDE.
        return real[:1]

    # ------------------------------------------------------------------
    # Les heures calmes
    # ------------------------------------------------------------------
    @api.model
    def _quiet_window_end(self, user, now=None):
        """Fin de la fenêtre calme si on y est, sinon False.

        Le calcul est celui de ``bf_veilleur.config.est_dans_la_fenetre`` :
        ``ZoneInfo`` plutôt qu'un décalage écrit en dur, ce qui fait suivre
        l'heure avancée sans intervention, et une fenêtre qui passe minuit se
        lit telle quelle.

        ⚠️ La fenêtre est lue dans le fuseau DU RÉGLAGE, jamais dans
        ``res.partner.tz``. Ce champ-là suit souvent le lieu de RÉSIDENCE : sur
        un compte réglé à ``Pacific/Auckland``, une fenêtre de 22 h à 8 h lue
        là vaut 6 h à 16 h en Amérique de l'Est, soit une journée de travail
        entière passée sous silence. Le mode a son propre fuseau explicite
        pour cette raison.
        """
        now = now or fields.Datetime.now()
        user = user.sudo()
        tz_name = user.bf_dnd_quiet_tz or "UTC"
        try:
            zone = ZoneInfo(tz_name)
        except (ZoneInfoNotFoundError, ValueError):
            _logger.warning(
                "bf.dnd : fuseau %r illisible pour %s, fenêtre traitée comme "
                "fermée.", tz_name, user.login)
            return False
        local = now.replace(tzinfo=ZoneInfo("UTC")).astimezone(zone)
        # Une fenêtre de largeur nulle est un réglage inachevé, pas une
        # fenêtre de 24 h : le test d'égalité plus bas ne fait rien taire.
        start = user.bf_dnd_quiet_start or 0.0
        end = user.bf_dnd_quiet_end or 0.0
        # ⚠️ `float_time` laisse saisir 24:00, et `replace(hour=24)` lève une
        # ValueError. Vingt-quatre heures et zéro heure désignent le même
        # instant de la journée : on normalise plutôt que de refuser.
        start = min(max(start, 0.0), 24.0) % 24.0
        end = min(max(end, 0.0), 24.0) % 24.0
        if start == end:
            return False
        hour = local.hour + local.minute / 60.0
        if start <= end:
            inside = start <= hour < end
        else:
            inside = hour >= start or hour < end
        if not inside:
            return False
        # L'heure de fin, ramenée en UTC. Quand la fenêtre passe minuit et
        # qu'on est avant minuit, la fin tombe le lendemain.
        end_local = local.replace(
            hour=int(end), minute=int(round((end - int(end)) * 60)),
            second=0, microsecond=0,
        )
        if end_local <= local:
            end_local = end_local + timedelta(days=1)
        return end_local.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)

    # ------------------------------------------------------------------
    # La file de retenue
    # ------------------------------------------------------------------
    @api.model
    def _hold_mail(self, user, emails):
        """Noter les courriels tus, pour le résumé de sortie.

        ⚠️ ``emails`` peut être une LISTE et non un jeu d'enregistrements :
        ``_notify_new_emails`` regroupe par persistance dans des listes
        Python, et c'est ce qu'il passe ici. Lire ``.ids`` dessus lève une
        ``AttributeError`` au moment précis où le mode devrait faire son
        travail, c'est-à-dire nulle part ailleurs qu'en production pendant une
        rencontre.
        """
        ids = [rec.id for rec in emails]
        if not ids:
            return
        Held = self.env["bf.dnd.held"].sudo()
        # ⚠️ Une seule requête pour tout le lot. Une reprise après panne
        # ramène des dizaines de lignes d'un coup, et une recherche par ligne
        # ferait payer la retenue plus cher que l'avis qu'elle remplace.
        already = set(Held.search([
            ("user_id", "=", user.id),
            ("kind", "=", "mail"),
            ("email_id", "in", ids),
            ("released_at", "=", False),
        ]).mapped("email_id").ids)
        fresh = [rid for rid in ids if rid not in already]
        if fresh:
            Held.create([
                {"user_id": user.id, "kind": "mail", "email_id": rid}
                for rid in fresh
            ])

    @api.model
    def _hold_reminder(self, user, event):
        """Noter un rappel tu, sous une clé qui survit à la série rasée.

        ⚠️ Le rappel lui-même n'est pas rejoué depuis cette file : à la
        sortie, le client rejoue ``/calendar/notify`` et le serveur rend
        l'alarme telle quelle, avec ses boutons de report. La file ne sert
        qu'à NOMMER la rencontre dans le résumé.
        """
        if not event:
            return
        Held = self.env["bf.dnd.held"].sudo()
        key = self.env["bf.calendar.reminder.ack"]._bf_reminder_key(event)
        existing = Held.search([
            ("user_id", "=", user.id),
            ("kind", "=", "reminder"),
            ("reminder_key", "=", key),
            ("released_at", "=", False),
        ], limit=1)
        if existing:
            return
        Held.create({
            "user_id": user.id,
            "kind": "reminder",
            "reminder_key": key,
            "event_name": event.name,
            "occurrence_start": event.start,
        })

    # ------------------------------------------------------------------
    # Le bus
    # ------------------------------------------------------------------
    @api.model
    def _push_state(self, user, state):
        """Annoncer la bascule au navigateur. Ne lève jamais.

        C'est ce message qui fait rejouer ``/calendar/notify`` au client, dans
        les deux sens. Voir l'en-tête du fichier.
        """
        partner = user.partner_id
        if not partner:
            return
        payload = {
            "active": bool(state["active"]),
            "reason": state["reason"] or False,
            "until": fields.Datetime.to_string(state["until"])
            if state["until"] else False,
        }
        try:
            self.env["bus.bus"].sudo()._sendone(
                partner, DND_STATE_CHANNEL, payload)
        except Exception:  # noqa: BLE001 - ne jamais casser l'appelant
            _logger.warning(
                "bf.dnd : envoi bus en échec pour le partenaire %s",
                partner.id, exc_info=True,
            )

    # ------------------------------------------------------------------
    # Le résumé de sortie
    # ------------------------------------------------------------------
    @api.model
    def _release(self, user):
        """Rendre un seul avis pour tout ce qui a été retenu, puis vider.

        ⚠️ La première ligne nomme les rencontres, pas les courriels : c'est
        la conséquence assumée du 09-01. Les rencontres dos à dos perdent leur
        préavis, le rappel de 19h45 sort à 20h00, et il faut alors dire
        d'abord CE QUI COMMENCE.
        """
        Held = self.env["bf.dnd.held"].sudo()
        held = Held.search([
            ("user_id", "=", user.id),
            ("released_at", "=", False),
        ], order="held_at")
        if not held:
            return
        mails = held.filtered(lambda h: h.kind == "mail" and h.email_id)
        reminders = held.filtered(lambda h: h.kind == "reminder")
        partner = user.partner_id
        if partner and (mails or reminders):
            payload = {
                "kind": "dnd_digest",
                "mail_count": len(mails),
                "email_ids": [h.email_id.id for h in mails[:DIGEST_PREVIEW_IDS]],
                "meetings": [h.event_name or "" for h in reminders][:DIGEST_NAMED],
                "reminder_count": len(reminders),
                "ttl_ms": DIGEST_TTL_MS,
            }
            # ⚠️ Par `bf.email.popup._sendone` et non par `bus.bus` en direct :
            # c'est lui qui pose `sent_ms`, l'horloge du serveur, sans quoi un
            # résumé rejoué le lendemain par `bus.bus` (rétention 24 h)
            # s'afficherait comme s'il venait d'arriver.
            self.env["bf.email.popup"]._sendone(partner, payload)
        held.write({"released_at": fields.Datetime.now()})

    # ------------------------------------------------------------------
    # Le battement
    # ------------------------------------------------------------------
    @api.model
    def _cron_bf_dnd_tick(self):
        """Reconnaître les bascules que personne n'a demandées.

        L'interrupteur manuel s'annonce lui-même à l'écriture. Le début et la
        fin d'une rencontre, et les bornes des heures calmes, n'ont personne
        pour les annoncer : c'est ici qu'on les voit passer.

        Le témoin ``bf_dnd_last_state`` sert UNIQUEMENT à reconnaître le
        changement. L'état, lui, se recalcule à chaque lecture ; un témoin
        périmé ne fait donc jamais taire ni parler à tort.
        """
        if not self._instance_enabled():
            return
        users = self.env["res.users"].sudo().search([
            ("active", "=", True),
            "|", "|", "|",
            ("bf_dnd_meetings", "=", True),
            ("bf_dnd_quiet_enabled", "=", True),
            ("bf_dnd_manual_until", "!=", False),
            ("bf_dnd_last_state", "=", True),
        ])
        now = fields.Datetime.now()
        for user in users:
            state = self._state_for(user, now=now)
            if bool(state["active"]) == bool(user.bf_dnd_last_state):
                continue
            user.write({"bf_dnd_last_state": state["active"]})
            self._push_state(user, state)
            if not state["active"]:
                self._release(user)


class BfDndHeld(models.Model):
    """Ce que le mode a tu, en attendant le résumé de sortie."""

    _name = "bf.dnd.held"
    _description = "Avis retenu par le mode « ne pas déranger »"
    _order = "held_at, id"

    user_id = fields.Many2one(
        "res.users", string="Personne", required=True, index=True,
        ondelete="cascade",
    )
    kind = fields.Selection(
        [("mail", "Courriel"), ("reminder", "Rappel d'agenda")],
        string="Genre", required=True,
    )
    email_id = fields.Many2one(
        "bf.email", string="Courriel", ondelete="cascade", index=True,
    )
    # ⚠️ Une chaîne, pas un lien typé : `calendar_nextcloud_sync` rase une
    # série récurrente et la recrée avec des identifiants neufs. La clé est
    # celle de `bf.calendar.reminder.ack`, l'UID CalDAV plus l'heure
    # d'occurrence.
    reminder_key = fields.Char(string="Clé de rappel", index=True)
    event_name = fields.Char(string="Rencontre")
    occurrence_start = fields.Datetime(string="Début de l'occurrence")
    held_at = fields.Datetime(
        string="Retenu le", required=True, default=fields.Datetime.now,
        index=True,
    )
    released_at = fields.Datetime(string="Rendu le", index=True)

    @api.autovacuum
    def _gc_bf_dnd_held(self):
        """Jeter ce qui a été rendu il y a plus d'une semaine."""
        cutoff = fields.Datetime.now() - timedelta(days=7)
        stale = self.sudo().search([
            ("released_at", "!=", False),
            ("released_at", "<", cutoff),
        ])
        if stale:
            stale.unlink()
