"""Per-attendee snooze + dismiss state for calendar reminders.

Standard Odoo's "Snooze" button on `calendar.alarm` notifications is a no-op:
it removes the toast from the screen but persists nothing, so the same alarm
re-fires on the next bus poll. We add real per-attendee state so the
notification is actually deferred or dismissed.

The cron `bf_calendar_snooze_refire` (data/calendar_reminder_cron.xml) clears
expired snoozes every minute and re-pushes the bus.bus alarm.
"""

import logging
from datetime import timedelta

import requests

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

NTFY_REMINDER_URL_PARAM = "bf_email.ntfy_reminder_url"
# Empty default: the cron short-circuits unless a tenant explicitly sets the
# ``bf_email.ntfy_reminder_url`` ir.config_parameter to a reachable endpoint.
NTFY_REMINDER_DEFAULT_URL = ""


SNOOZE_PRESETS = {
    "5m": 5,
    "15m": 15,
    "1h": 60,
    "3h": 180,
}


class CalendarAttendee(models.Model):
    _inherit = "calendar.attendee"

    bf_snoozed_until = fields.Datetime(
        string="Snoozed until",
        index=True,
        help="If set in the future, the calendar alarm bus.bus notification "
             "is suppressed for this attendee. The 1-min cron clears the "
             "field once the timestamp passes and re-pushes the alarm.",
    )
    bf_dismissed_at = fields.Datetime(
        string="Dismissed at",
        help="Timestamp at which this attendee dismissed the reminder for "
             "the current alarm trigger. Compared against the alarm "
             "notify_at to suppress repeat firing.",
    )
    bf_imip_reply_stamp = fields.Datetime(
        string="Emailed answer stamp",
        copy=False,
        help="DTSTAMP of the last guest answer (iMIP REPLY) applied to this "
             "line. An older answer that arrives later is ignored: the last "
             "answer GIVEN wins, not the last one read.",
    )
    bf_ntfy_pushed_at = fields.Datetime(
        string="ntfy pushed at",
        help="Timestamp of the last ntfy push for this attendee. Used by the "
             "1-min push cron to dedupe pushes for the same alarm window.",
    )

    # ------------------------------------------------------------------
    # Snooze / dismiss actions (called from OWL component or controller)
    # ------------------------------------------------------------------

    @api.model
    def _bf_attendee_for_user(self, event_id):
        """Return the attendee row matching the current user for ``event_id``."""
        partner = self.env.user.partner_id
        if not partner:
            raise UserError(_("No partner attached to current user."))
        attendee = self.search(
            [("event_id", "=", int(event_id)), ("partner_id", "=", partner.id)],
            limit=1,
        )
        if not attendee:
            raise UserError(_(
                "You are not an attendee of event %s.", event_id
            ))
        return attendee

    def _bf_record_reminder_ack(self, **vals):
        """Doubler l'état de la fiche participant dans l'accusé durable.

        La fiche participant disparaît dès que ``calendar_nextcloud_sync``
        réimporte la série récurrente : elle rase la récurrence et toutes ses
        occurrences avant de les recréer avec des ``id`` neufs. L'accusé, lui,
        est classé sous l'UID CalDAV de la série et l'heure de l'occurrence,
        que le ``.ics`` conserve. Voir ``bf_calendar_reminder_ack.py``.
        """
        for attendee in self:
            if not attendee.partner_id or not attendee.event_id:
                continue
            self.env["bf.calendar.reminder.ack"]._bf_record(
                attendee.partner_id, attendee.event_id, **vals
            )

    def _bf_broadcast_reminder_closed(self, event_id, reason):
        """Dire aux AUTRES onglets de retirer le rappel de cet événement.

        Le toast n'existe que dans le navigateur qui l'a reçu : reporter ou
        marquer vu dans une fenêtre laissait le même rappel affiché dans
        toutes les autres, chacune attendant un geste déjà posé. On passe
        donc par le bus, sur le canal du participant — toutes ses sessions y
        sont abonnées — et le client ferme le toast correspondant.

        L'envoi part au commit de la transaction, donc après l'écriture de
        l'état : une fenêtre qui rejouerait `/calendar/notify` juste après
        lirait de toute façon un rappel déjà éteint.
        """
        self.env["bus.bus"]._sendone(
            self.partner_id,
            "bf_calendar_reminder/close",
            {"event_id": int(event_id), "reason": reason},
        )
        # Et le téléphone. Un report ou un « vu » posé au bureau
        # laissait la notification de Symbifox Mobile affichée.
        for attendee in self:
            attendee._bf_push_mobile({"type": "rappel_clear", "event_id": int(event_id),
                                      "reason": reason})

    @api.model
    def bf_snooze(self, event_id, minutes=None, until=None):
        """Snooze the reminder for ``event_id`` for the current user.

        Either ``minutes`` (int from SNOOZE_PRESETS) or ``until`` (ISO
        datetime string) must be provided.
        """
        attendee = self._bf_attendee_for_user(event_id)
        if minutes is not None:
            target = fields.Datetime.now() + timedelta(minutes=int(minutes))
        elif until:
            target = fields.Datetime.from_string(until)
        else:
            raise UserError(_("Provide either minutes or until."))
        attendee.write({
            "bf_snoozed_until": target,
            "bf_dismissed_at": False,
        })
        attendee._bf_record_reminder_ack(
            snoozed_until=target, dismissed_at=False,
        )
        # Ack the partner so the standard mechanism stops firing this alarm
        # until the snooze expires (the cron will re-push).
        # ⚠️ sudo : écrire sur res.partner demande « Contact Creation », que la
        # plupart des usagers internes n'ont pas. Sans ça, reporter son propre
        # rappel rend une AccessError. Odoo fait la même chose dans
        # /calendar/notify_ack, avec le même sudo.
        attendee.partner_id.sudo().write({
            "calendar_last_notif_ack": fields.Datetime.now(),
        })
        attendee._bf_broadcast_reminder_closed(event_id, "snooze")
        return {"snoozed_until": fields.Datetime.to_string(target)}

    @api.model
    def bf_dismiss(self, event_id):
        """Definitively dismiss the reminder for ``event_id`` (no re-fire)."""
        attendee = self._bf_attendee_for_user(event_id)
        now = fields.Datetime.now()
        attendee.write({
            "bf_dismissed_at": now,
            "bf_snoozed_until": False,
        })
        attendee._bf_record_reminder_ack(dismissed_at=now, snoozed_until=False)
        attendee.partner_id.sudo().write({
            "calendar_last_notif_ack": now,
        })
        attendee._bf_broadcast_reminder_closed(event_id, "dismiss")
        return {"dismissed_at": fields.Datetime.to_string(now)}

    # ------------------------------------------------------------------
    # Cron — re-fire snoozed reminders whose snooze window has elapsed
    # ------------------------------------------------------------------

    @api.model
    def _bf_refire_expired_snoozes(self):
        """Find attendees with elapsed snooze, clear, push bus.bus alarm.

        Called every minute by ``ir_cron_bf_calendar_snooze_refire``.
        """
        now = fields.Datetime.now()
        expired = self.search([
            ("bf_snoozed_until", "!=", False),
            ("bf_snoozed_until", "<=", now),
            ("event_id.stop", ">", now),
        ])
        if not expired:
            return
        partners = expired.partner_id
        # Clear so the gate in alarm_manager re-opens for these attendees.
        # Also rewind the partner ack so get_next_notif() returns the alarm
        # again (it filters out alarms with notify_at <= calendar_last_notif_ack).
        expired.write({"bf_snoozed_until": False})
        # 🔴 L'accusé durable aussi. Le filtre du gestionnaire
        # d'alarmes recopie l'accusé sur la fiche : laissé en place, le report
        # revenait sur la fiche à la lecture suivante, et la relance aussi.
        expired._bf_record_reminder_ack(snoozed_until=False, ntfy_pushed_at=False)
        for partner in partners:
            partner.write({
                "calendar_last_notif_ack": now - timedelta(days=1),
            })
        # Allow re-push to ntfy for the new alarm window
        expired.write({"bf_ntfy_pushed_at": False})
        self.env["calendar.alarm_manager"]._notify_next_alarm(partners.ids)
        # Le téléphone resonne lui aussi à la fin du report : la
        # tâche ntfy ne regarde qu'une fenêtre d'une minute autour de l'alarme,
        # où l'échéance d'un report ne tombe presque jamais.
        for attendee in expired:
            if attendee.event_id and attendee.state != "declined":
                attendee._bf_push_rappel_mobile(attendee.event_id)

    # ------------------------------------------------------------------
    # Cron — push ntfy reminder for imminent alarms (gated by snooze state)
    # ------------------------------------------------------------------

    @api.model
    def _bf_reminder_candidate_events(self, now):
        """Les rencontres à venir dont un rappel peut sonner au téléphone.

        Les rappels qu'un invité règle dans Thunderbird ou Apple ne sont pas
        dans `alarm_ids` : `bf_dav_server` les garde par personne
        dans `bf.dav.user.data` et ajoute leurs rencontres.
        """
        events = self.env["calendar.event"].search([
            ("active", "=", True),
            ("stop", ">", now),
            ("alarm_ids.alarm_type", "=", "notification"),
            ("start", ">", now),
        ])
        # 🔴 Par le modèle, pas par héritage : `bf_dav_server` ne dépend pas de
        # ce module, et s'il se chargeait avant, cette méthode masquerait la
        # sienne sans bruit.
        ud = self.env.get("bf.dav.user.data")
        if ud is not None and hasattr(ud, "_bf_reminder_events"):
            events |= ud.sudo()._bf_reminder_events(now)
        return events

    def _bf_reminder_triggers(self, event):
        """Les couples (heure du rappel, rappel) qui font sonner le téléphone de
        ce participant pour ``event`` : les rappels « notification » de la
        rencontre, ou ceux que la personne a réglés elle-même dans son
        application (`bf_dav_server`). Le rappel n'a besoin que de
        ``duration_minutes``."""
        self.ensure_one()
        ud = self.env.get("bf.dav.user.data")
        if ud is not None and hasattr(ud, "_bf_reminder_triggers"):
            own = ud.sudo()._bf_reminder_triggers(self, event)
            if own is not None:
                return own
        return [
            (event.start - timedelta(minutes=alarm.duration_minutes), alarm)
            for alarm in event.alarm_ids
            if alarm.alarm_type == "notification"
        ]

    @api.model
    def _bf_push_ntfy_for_imminent_alarms(self):
        """Push a ntfy notification for each attendee whose notification alarm
        fires in the next minute, unless they have snoozed/dismissed it or it
        was already pushed for this window.
        """
        url = self.env["ir.config_parameter"].sudo().get_param(
            NTFY_REMINDER_URL_PARAM, NTFY_REMINDER_DEFAULT_URL,
        )
        # Sans relais ntfy, on continue quand même : une personne
        # qui a Symbifox Mobile reçoit le rappel par lui.
        now = fields.Datetime.now()
        # The cron fires a reminder up to PUSH_LEAD *before* notify_at, so the
        # de-dup guards below must allow for that lead. Keeping the two in one
        # constant is what stops them drifting apart again. BF task.
        PUSH_LEAD = timedelta(seconds=70)
        horizon = now + PUSH_LEAD  # cron runs every minute
        # Catch alarms that fire in the next ~70s OR fired up to 60s ago
        # (in case the previous cron tick missed them).
        floor = now - timedelta(seconds=60)
        events = self._bf_reminder_candidate_events(now)
        for event in events:
            for attendee in event.attendee_ids:
                if attendee.state == "declined":
                    continue
                for notify_at, alarm in attendee._bf_reminder_triggers(event):
                    if not (floor <= notify_at <= horizon):
                        continue
                    # L'accusé durable rattrape ce que la fiche participant a
                    # perdu quand la série récurrente a été rasée et recréée :
                    # sans lui, le téléphone recevait une deuxième fois le même
                    # rappel, quelques minutes après avoir été écarté.
                    ack = self.env["bf.calendar.reminder.ack"]._bf_find(
                        attendee.partner_id, event,
                    )
                    snoozed_until = max(
                        [s for s in (attendee.bf_snoozed_until, ack.snoozed_until) if s],
                        default=False,
                    )
                    dismissed_at = max(
                        [d for d in (attendee.bf_dismissed_at, ack.dismissed_at) if d],
                        default=False,
                    )
                    ntfy_pushed_at = max(
                        [n for n in (attendee.bf_ntfy_pushed_at, ack.ntfy_pushed_at) if n],
                        default=False,
                    )
                    if snoozed_until and snoozed_until > now:
                        continue
                    # Both guards subtract PUSH_LEAD, and that subtraction is
                    # the whole point. horizon reaches PUSH_LEAD ahead of
                    # notify_at, so a reminder already pushed for this window
                    # carries a timestamp EARLIER than notify_at. Comparing
                    # against notify_at alone could therefore never match, and
                    # every reminder went out exactly twice — once on the tick
                    # that fired it early, once on the next tick. Confirmed
                    # 2026-08-28 on event 214702: identical relay payload at
                    # 16:44:00 and 16:45:20. Same reasoning for a dismissal:
                    # the user dismisses the early push, still before notify_at.
                    if (dismissed_at
                            and dismissed_at >= notify_at - PUSH_LEAD):
                        continue
                    if (ntfy_pushed_at
                            and ntfy_pushed_at >= notify_at - PUSH_LEAD):
                        continue
                    # Symbifox Mobile d'abord, avec ses boutons ; ntfy seulement
                    # pour qui n'a pas Mobile, sinon le téléphone sonnerait deux fois.
                    if attendee._bf_push_rappel_mobile(event, alarm):
                        continue
                    if url:
                        self._bf_push_ntfy_attendee(url, attendee, event, alarm)

    # ------------------------------------------------------------------
    # Symbifox Mobile — le rappel et son effacement
    # ------------------------------------------------------------------

    def _bf_utilisateur_interne(self):
        """L'usager interne du participant, ou rien (invité externe, portail)."""
        self.ensure_one()
        return self.partner_id.user_ids.filtered(lambda u: u.active and not u.share)[:1]

    # La première version de Symbifox Mobile qui sait afficher « rappel ». Une
    # plus ancienne ignore ce type : lui envoyer le rappel au lieu de ntfy, ce
    # serait ne plus rien recevoir du tout jusqu'à la mise à jour.
    VERSION_MOBILE_RAPPELS = (3, 5, 0)

    @staticmethod
    def _bf_version(texte):
        try:
            return tuple(int(x) for x in (texte or "").split("-")[0].split(".")[:3])
        except ValueError:
            return ()

    def _bf_push_mobile(self, payload, version_min=None):
        """Pousse ``payload`` vers les appareils Symbifox Mobile du participant.

        Rend vrai si au moins un appareil l'a reçu en charge. Ne lève jamais :
        un rappel manqué ne doit pas faire échouer un report ou une tâche.
        """
        self.ensure_one()
        usager = self._bf_utilisateur_interne()
        if not usager:
            return False
        push = self.env["bf.email.unifiedpush"].sudo()
        try:
            appareils = push._devices(usager)
            if version_min:
                a_jour = [
                    a for a in appareils
                    if self._bf_version(getattr(a, "app_version", "")) >= version_min
                ]
                # 🔴 Un téléphone trop ancien ENCORE UTILISÉ (vu dans la semaine)
                # dépend de ntfy : on rend faux pour que ntfy parte aussi, sinon
                # ce téléphone ne recevrait plus rien. Un vieil appareil oublié
                # ne compte pas, sinon le téléphone à jour sonnerait deux fois.
                recent = fields.Datetime.now() - timedelta(days=7)
                anciens_actifs = [
                    a for a in appareils
                    if a not in a_jour and getattr(a, "last_seen", False)
                    and a.last_seen >= recent
                ]
                appareils = a_jour
            else:
                anciens_actifs = []
            if not appareils:
                return False
            # Reçu par au moins un appareil (2xx), sinon ntfy prend le relais :
            # un envoi échoué ne doit pas compter comme parti (relevé à l'audit).
            recus = push._envoyer_a(appareils, payload) or 0
            return recus > 0 and not anciens_actifs
        except Exception:
            _logger.warning("Rappel vers Mobile : envoi échoué (participant %s).",
                            self.id, exc_info=True)
            return False

    def _bf_push_rappel_mobile(self, event, alarm=None):
        """Le rappel lui-même, avec de quoi reporter, marquer vu et ouvrir la fiche.

        Rend vrai s'il est parti vers Mobile ; l'horodatage d'envoi est alors
        posé comme pour ntfy, et sert de garde contre le double envoi.
        """
        self.ensure_one()
        payload = {
            "type": "rappel",
            "event_id": event.id,
            "key": self.env["bf.calendar.reminder.ack"]._bf_reminder_key(event),
            "name": event.name or "",
            "start": fields.Datetime.to_string(event.start) if event.start else "",
            "allday": bool(event.allday),
            "location": event.location or "",
            "videocall": event.videocall_location or "",
            "duration_minutes": alarm.duration_minutes if alarm else 0,
        }
        if not self._bf_push_mobile(payload, version_min=self.VERSION_MOBILE_RAPPELS):
            return False
        pushed_at = fields.Datetime.now()
        self.write({"bf_ntfy_pushed_at": pushed_at})
        self._bf_record_reminder_ack(ntfy_pushed_at=pushed_at)
        return True

    def _bf_push_ntfy_attendee(self, url, attendee, event, alarm):
        """POST one ntfy reminder to the webhook relay."""
        payload = {
            "_id": event.id,
            "_model": "calendar.event",
            "name": event.name or "",
            "partner_name": attendee.partner_id.name or "",
            "partner_email": attendee.partner_id.email or "",
            "start": fields.Datetime.to_string(event.start) if event.start else "",
            "duration_minutes": alarm.duration_minutes,
        }
        try:
            requests.post(url, json=payload, timeout=5)
        except Exception as exc:
            _logger.warning(
                "ntfy push failed for attendee=%s event=%s: %s",
                attendee.id, event.id, exc,
            )
            return
        pushed_at = fields.Datetime.now()
        attendee.write({"bf_ntfy_pushed_at": pushed_at})
        attendee._bf_record_reminder_ack(ntfy_pushed_at=pushed_at)

    # ------------------------------------------------------------------
    # Rien ne part jamais vers les invités d'une invitation qu'on a REÇUE
    # ------------------------------------------------------------------

    def _send_mail_to_attendees(self, mail_template, force_send=False):
        """Écarte tout destinataire d'un événement né d'une invitation reçue.

        🔴 C'est la garde qui rend l'ajout des invités possible. Jusqu'ici,
        `_imip_create_vals` n'inscrivait QUE le propriétaire, et son commentaire
        disait pourquoi : un invité externe sur un événement dont le propriétaire est
        l'organisateur ferait ré-inviter tout le monde par le greffon iMIP de
        Nextcloud. La peur était fondée, et elle tient : les invités ne sont pas
        inscrits (ils vivent dans la description), et la garde protège
        celui qu'on ajoute à la main.

        Un seul point de passage couvre l'invitation, l'avis de déplacement du
        cœur, le rappel par courriel et le bouton « Envoyer un courriel aux
        participants » : ils appellent tous cette méthode. L'avis de changement
        de `bf_calendar_invite`, qui ne passe pas par ici, a sa garde jumelle
        dans `_bf_notice_recipients`.

        Le propriétaire de la boîte, lui, reste joignable : c'est SON agenda, et
        un rappel par courriel qu'il s'est posé doit lui arriver.
        """
        if "x_imip_uid" not in self.env["calendar.event"]._fields:
            return super()._send_mail_to_attendees(
                mail_template, force_send=force_send)
        recues = self.filtered(lambda a: a.event_id.x_imip_uid)
        if not recues:
            return super()._send_mail_to_attendees(
                mail_template, force_send=force_send)
        proprietaires = recues.filtered(
            lambda a: a.partner_id
            and a.partner_id == a.event_id.user_id.partner_id
        )
        ecartes = recues - proprietaires
        if ecartes:
            # Écrit au journal plutôt que tombé en silence : le jour où
            # quelqu'un cherche pourquoi un invité n'a rien reçu, la réponse
            # doit se trouver dans le journal de la requête qui l'a écarté.
            _logger.info(
                "bf_email_management: %s destinataire(s) écarté(s) sur une "
                "invitation reçue (événements %s) : ses invités sont ceux de "
                "son organisateur, pas les nôtres.",
                len(ecartes), recues.event_id.ids,
            )
        garder = (self - recues) | proprietaires
        if not garder:
            return False
        return super(CalendarAttendee, garder)._send_mail_to_attendees(
            mail_template, force_send=force_send)
