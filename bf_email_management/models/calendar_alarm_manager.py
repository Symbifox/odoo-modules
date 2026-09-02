"""Filtrer les rappels calendrier poussés sur bus.bus.

On surcharge ``do_check_alarm_for_one_date`` (appelée par ``get_next_notif``,
elle-même appelée par le sondage ``/calendar/notify`` et par
``_notify_next_alarm``) pour écarter deux familles d'alertes :

1. celles que le participant a déjà reportées ou marquées vues. L'état est lu
   sur la fiche participant ET sur l'accusé durable
   ``bf.calendar.reminder.ack``, parce que la fiche participant ne survit pas à
   la réimportation d'une série récurrente ;
2. celles dont la rencontre est déjà TERMINÉE. ``bus.bus`` rejoue jusqu'à 24 h
   de messages à la reconnexion (``_poll`` filtre sur ``id > last``, sans borne
   de date, et le client garde ``last_notification_id`` en localStorage), donc
   un portable rouvert le lendemain se faisait déverser les rappels de la
   veille.

⚠️ Le garde-fou du point 2 s'arrête à la fin de la rencontre, pas à son début :
ouvrir son portable à 08h55 pour une rencontre de 09h00 doit encore afficher le
rappel de 08h45.
"""

import time

from odoo import fields, models


class AlarmManager(models.AbstractModel):
    _inherit = "calendar.alarm_manager"

    def do_check_alarm_for_one_date(self, one_date, event, event_maxdelta,
                                    in_the_next_X_seconds, alarm_type,
                                    after=False, missing=False):
        result = super().do_check_alarm_for_one_date(
            one_date, event, event_maxdelta, in_the_next_X_seconds,
            alarm_type, after=after, missing=missing,
        )
        if not result or alarm_type != "notification":
            return result
        now = fields.Datetime.now()
        if event.stop and event.stop <= now:
            return []
        partner = self.env.user.partner_id
        if not partner:
            return result
        dismissed_at, snoozed_until = self._bf_reminder_state(event, partner)
        if snoozed_until and snoozed_until > now:
            return []
        filtered = []
        for alert in result:
            notify_at = alert.get("notify_at")
            if dismissed_at and notify_at and dismissed_at >= notify_at:
                continue
            filtered.append(alert)
        return filtered

    def _bf_reminder_state(self, event, partner):
        """État « vu / reporté » du participant, fiche et accusé confondus.

        Retourne le couple ``(dismissed_at, snoozed_until)`` le plus récent des
        deux sources. Quand l'accusé durable sait quelque chose que la fiche
        participant ignore, c'est que la série vient d'être rasée et recréée :
        on recopie alors l'état sur la fiche neuve, pour que les crons qui la
        lisent (report échu, poussée ntfy, pont CalDAV) restent d'accord avec
        la porte sans qu'aucun d'eux ait à connaître l'accusé.
        """
        attendee = event.attendee_ids.filtered(lambda a: a.partner_id == partner)
        ack = self.env["bf.calendar.reminder.ack"]._bf_find(partner, event)
        if not attendee and not ack:
            return False, False
        dismissed_at = max(
            [d for d in (attendee[:1].bf_dismissed_at, ack.dismissed_at) if d],
            default=False,
        )
        snoozed_until = max(
            [s for s in (attendee[:1].bf_snoozed_until, ack.snoozed_until) if s],
            default=False,
        )
        if attendee:
            backfill = {}
            if dismissed_at and attendee.bf_dismissed_at != dismissed_at:
                backfill["bf_dismissed_at"] = dismissed_at
            if snoozed_until and attendee.bf_snoozed_until != snoozed_until:
                backfill["bf_snoozed_until"] = snoozed_until
            if ack.ntfy_pushed_at and attendee.bf_ntfy_pushed_at != ack.ntfy_pushed_at:
                backfill["bf_ntfy_pushed_at"] = ack.ntfy_pushed_at
            if backfill:
                attendee.sudo().write(backfill)
        return dismissed_at, snoozed_until

    def do_notif_reminder(self, alert):
        """Horodater la charge utile avec l'horloge du SERVEUR.

        ``bus.bus`` rejoue jusqu'à 24 h de messages à la reconnexion, et le
        ``timer`` calculé ici n'est valable qu'à l'instant de l'envoi : rejoué,
        il arrive négatif, et ``setTimeout`` le ramène à zéro, donc le rappel
        surgit sur-le-champ. ``sent_ms`` donne au client de quoi reconnaître un
        message périmé. Même base que ``Date.now()`` côté navigateur.
        """
        notif = super().do_notif_reminder(alert)
        if notif:
            notif["sent_ms"] = int(time.time() * 1000)
        return notif
