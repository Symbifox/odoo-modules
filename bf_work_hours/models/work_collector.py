from datetime import timedelta

from odoo import _, api, models

from .engine import Item

# A meeting of 8 hours or more is a block in the calendar, not a meeting.
MEETING_MAX = timedelta(hours=8)
# Sources captured live, only once the hours are on: nothing of them exists from before,
# so taking in the earlier days finds none (bf_work_hours_timer adds "timer").
LIVE_SOURCES = ("presence", "timer")


class WorkCollector(models.AbstractModel):
    """Where the traces of a person come from. One method, extended per source.

    Each bridge module adds its source by overriding ``_bf_collect`` and
    ``_bf_point_minutes``. A source belongs here only if it proves a hand:
    the chatter and field changes do not, since an agent or a script writing
    with the person's API key signs them with the person's name.
    """

    _name = "bf.work.collector"
    _description = "Working time: sources of traces"

    @api.model
    def _bf_source_descriptions(self):
        """(key, label, what it reads in plain words) of each installed source, for the notice."""
        return [
            ("presence", _("Browser activity"), _("Whether you used the keyboard or the mouse in Odoo during "
                                                  "the last two minutes, reported once a minute by your own "
                                                  "browser session. Not what you typed, not which page.")),
            ("calendar", _("Calendar"), _("Start and end of the meetings with at least one other person that "
                                          "you accepted yourself (or that a calendar synchronization accepted "
                                          "for you), for the hours they had when they were accepted.")),
            ("terminal", _("Your own tools"), _("Only if you install one yourself: the moments a local tool "
                                                "of yours reports, such as the prompts typed in a terminal. "
                                                "Not what you typed.")),
        ]

    @api.model
    def _bf_notice_visibility(self):
        """Hook for bridges: more lines under "Who sees it" in the notice."""
        return []

    @api.model
    def _bf_point_minutes(self):
        """Minutes a point of each source is worth (the time it took to produce it)."""
        return {}

    @api.model
    def _bf_collect(self, user, start, stop):
        """Traces of ``user`` overlapping [start, stop) (naive UTC), as ``Item``s."""
        items = []
        signals = self.env["bf.work.signal"].sudo().search([
            ("user_id", "=", user.id), ("date_start", "<", stop), ("date_stop", ">=", start),
        ])
        for signal in signals:
            kind = "point" if signal.date_start == signal.date_stop else "span"
            items.append(Item(signal.date_start, signal.date_stop, signal.source, kind))
        items += self._bf_collect_calendar(user, start, stop)
        return items

    @api.model
    def _bf_collect_calendar(self, user, start, stop):
        """Meetings the person accepted, with at least one other attendee, under 8 hours.

        "Free" and all-day entries are not meetings; neither is a block one
        puts in one's own calendar (a single attendee).
        """
        attendees = self.env["calendar.attendee"].sudo().search([
            ("partner_id", "=", user.partner_id.id),
            ("state", "=", "accepted"),
            ("bf_work_state_by", "in", ("self", "odoo")),
            # 🔴 An archived occurrence (a rewritten series leaves hundreds) is not a meeting.
            ("event_id.active", "=", True),
            ("event_id.start", "<", stop),
            ("event_id.stop", ">", start),
            ("event_id.allday", "=", False),
            ("event_id.show_as", "=", "busy"),
        ])
        items = []
        # 🔴 Anyone who can edit an event can write the answer of every attendee,
        # move the event, hand a line to someone else or name someone organizer.
        # Only an acceptance the person gave (or Odoo, in a job) counts, and only
        # for the hours it was given for: see calendar_attendee.py. An answer
        # without a stamp counts for nothing (the installation stamps the past).
        for attendee in attendees:
            event = attendee.event_id
            if len(event.partner_ids) < 2 or not event.stop or event.stop - event.start >= MEETING_MAX:
                continue
            if "bf_event_status" in event._fields and event.bf_event_status == "cancelled":
                continue  # cancelled but kept active (bf_calendar_invite, bf_appointment)
            start = max(event.start, attendee.bf_work_state_start or event.start)
            stop = min(event.stop, attendee.bf_work_state_stop or event.stop)
            if stop > start:
                items.append(Item(start, stop, "calendar", "meeting"))
        return items
