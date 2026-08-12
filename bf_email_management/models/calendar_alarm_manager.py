"""Gate calendar alarm bus.bus notifications on per-attendee snooze state.

We override ``do_check_alarm_for_one_date`` (called by ``get_next_notif``,
itself called by the bus poll and by ``_notify_next_alarm``) and skip alerts
for events where the current user's attendee row has an active snooze or a
``bf_dismissed_at`` that postdates the alarm's ``notify_at``.
"""

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
        partner = self.env.user.partner_id
        if not partner:
            return result
        attendee = event.attendee_ids.filtered(lambda a: a.partner_id == partner)
        if not attendee:
            return result
        now = fields.Datetime.now()
        snoozed = attendee.bf_snoozed_until and attendee.bf_snoozed_until > now
        filtered = []
        for alert in result:
            if snoozed:
                continue
            notify_at = alert.get("notify_at")
            if (attendee.bf_dismissed_at and notify_at
                    and attendee.bf_dismissed_at >= notify_at):
                continue
            filtered.append(alert)
        return filtered
