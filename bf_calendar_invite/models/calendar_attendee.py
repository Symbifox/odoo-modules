"""Stand core's own "date updated" notice down, now that we send one.

Core sends `calendar.calendar_template_meeting_changedate` from inside
`calendar.event.write()`, with `force_send=True`, whenever `start` is in the
values. It is the only notice Odoo sends by itself, and it is the one member of
this family that was never dressed: no brand, no before-and-after, no mention of
the room or the joining link, and no way to decide against sending it.

Leaving it in place while this module sends its own would write to every guest
twice for the same move, from two different templates, one of which contradicts
the other by being silent about everything but the start time.

⚠️ Only that template is intercepted. The invitation and the reminder go out
untouched: they are core's job, they work, and a broad "no calendar mail"
switch here would be a second, invisible copy of `calendar.block_mail`.
"""

import logging

from odoo import models

_logger = logging.getLogger(__name__)


class CalendarAttendee(models.Model):
    _inherit = "calendar.attendee"

    def _send_mail_to_attendees(self, mail_template, force_send=False):
        changedate = self.env.ref(
            "calendar.calendar_template_meeting_changedate",
            raise_if_not_found=False,
        )
        if (
            changedate
            and mail_template
            and mail_template.id == changedate.id
            and not self.env.context.get("bf_allow_core_changedate")
        ):
            # Logged rather than dropped in silence. The day someone wonders
            # why a move produced no mail at all, the answer has to be findable
            # from the log of the request that made the move.
            _logger.info(
                "bf_calendar_invite: core's change notice stood down for "
                "attendees %s; bf_calendar_invite sends its own.", self.ids,
            )
            return False
        return super()._send_mail_to_attendees(mail_template, force_send=force_send)

    def do_accept(self):
        """Poser le « C » quand l'invité répond lui-même oui.

        Le badge « C » dans le coin de la rencontre est le statut CONFIRMÉ du
        module, et c'est ce que le rappel de la veille demande à l'invité : le
        lien de la page d'invitation le ramène ici, et sa réponse est ce qui
        confirme. On le pose donc à SA réponse, et non au départ du rappel : à
        l'envoi, c'est `bf_confirm_reminder_sent` qui marque la rencontre, et
        les deux marques séparées disent d'un coup d'oeil qui a été relancé et
        qui a répondu.

        Borné aux rencontres dont une étiquette arme le rappel. Ailleurs, le
        statut reste ce que son organisateur en a fait : une rencontre interne
        qu'un collègue accepte n'a pas à changer de statut, et le statut part
        dans le `.ics` (`STATUS`) comme dans la poussée CalDAV.

        `sudo()` parce que l'appelant courant est souvent le visiteur public de
        `/calendar/meeting/view`, qui n'a aucun droit d'écriture sur la
        rencontre.
        """
        res = super().do_accept()
        for attendee in self:
            event = attendee.event_id
            if not event or not event.bf_confirm_reminder_armed:
                continue
            if event.bf_event_status == "confirmed":
                continue
            # 🔴 JAMAIS une rencontre annulée. Mesuré par la relecture adverse
            # du 2026-11-02 : l'invité qui rouvrait le rappel resté dans sa
            # boîte et cliquait Accepter faisait repasser la rencontre en
            # « confirmée », lui rendait son créneau « occupé », remettait un
            # `STATUS:CONFIRMED` dans la poussée CalDAV, et laissait la raison
            # d'annulation en place. La route de série du coeur
            # (`/calendar/recurrence/accept`) le faisait pour TOUTES les
            # occurrences d'un coup.
            if event.bf_event_status == "cancelled":
                _logger.info(
                    "bf_calendar_invite: %s a accepté la rencontre annulée %s; "
                    "le statut reste « annulée ».",
                    attendee.common_name, event.id,
                )
                continue
            # Seule la réponse d'un invité EXTERNE confirme : le badge dit
            # « l'invité a répondu », et un collègue qui accepte n'est pas
            # l'invité.
            if attendee not in event._bf_outside_guests():
                continue
            event.sudo().write({"bf_event_status": "confirmed"})
        return res
