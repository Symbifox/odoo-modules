from odoo import SUPERUSER_ID, api, fields, models
from . import tools

STATE_BY = [
    ("self", "The person"),
    ("odoo", "Odoo (calendar synchronization)"),
    ("other", "Someone else"),
]
STAMP_FIELDS = ("bf_work_state_by", "bf_work_state_start", "bf_work_state_stop")


class CalendarAttendee(models.Model):
    """Who answered for the person, and for which hours.

    Anyone who can edit an event can write the answer of every attendee, and
    Odoo rewrites ``write_uid`` whenever a stored field of the line is
    recomputed: neither says who accepted. This stamp does, at the moment the
    answer is written, with the hours of the meeting at that moment. A meeting
    moved after the answer counts only for the hours that were accepted.

    The stamp itself is written by this code alone: any value a caller sends,
    in ``vals`` or as a context default, is dropped.
    """

    _inherit = "calendar.attendee"

    bf_work_state_by = fields.Selection(STATE_BY, string="Answered by", readonly=True, copy=False)
    bf_work_state_start = fields.Datetime(string="Answered for (start)", readonly=True, copy=False)
    bf_work_state_stop = fields.Datetime(string="Answered for (end)", readonly=True, copy=False)

    def _bf_work_answered_by(self):
        """The person in their own session; Odoo for a job or a sync; anyone else otherwise.

        🟠 Under superuser WITH a request (the invitation link: a GET that whoever
        holds the token, which any internal user can read, can fire, even in the
        person's own session through a tag in a message), it is someone else.
        """
        self.ensure_one()
        person_uids = self.partner_id.user_ids.ids
        if self.env.su:
            if not tools.request:
                return "odoo"
            # 🟠 The invitation link is a GET: a tag in a message can fire it in the
            # person's own session. An answer given that way is not proof of a hand.
            return "other"
        # The person's own API key answers for the person (their agent, their
        # script); an impersonator does not.
        if self.env.uid in person_uids and not tools.impersonating():
            return "self"
        return "other"

    def _bf_work_stamp(self):
        for attendee in self:
            stamp = {"bf_work_state_by": attendee._bf_work_answered_by(),
                     "bf_work_state_start": attendee.event_id.start,
                     "bf_work_state_stop": attendee.event_id.stop}
            # Written as superuser through the parent write: the stamp is not the caller's to write.
            super(CalendarAttendee, attendee.sudo().with_context(bf_work_stamping=True)).write(stamp)

    def _bf_work_strip(self, vals):
        if self.env.su and self.env.context.get("bf_work_stamping"):
            return vals
        return {k: v for k, v in vals.items() if k not in STAMP_FIELDS}

    @api.model_create_multi
    def create(self, vals_list):
        clean = {k: v for k, v in self.env.context.items()
                 if not (k.startswith("default_") and k[len("default_"):] in STAMP_FIELDS)}
        attendees = super(CalendarAttendee, self.with_context(clean)).create(
            [self._bf_work_strip(vals) for vals in vals_list])
        attendees.filtered(lambda a: a.state == "accepted")._bf_work_stamp()
        return attendees

    def write(self, vals):
        vals = self._bf_work_strip(vals)
        res = super().write(vals)
        # 🔴 Moving a line to another person or another event is a new answer: a
        # line accepted by Bob, handed to Alice, is not Alice's acceptance.
        if {"state", "partner_id", "event_id"} & set(vals):
            self._bf_work_stamp()
        return res

    def _bf_work_follow_move(self):
        """The meeting was moved: the person (or Odoo, for a sync) who answered and
        moves it carries the answer to the new hours; anyone else does not."""
        for attendee in self.filtered(lambda a: a.state == "accepted" and a.bf_work_state_by in ("self", "odoo")):
            if attendee._bf_work_answered_by() == attendee.bf_work_state_by:
                super(CalendarAttendee, attendee.sudo()).write({
                    "bf_work_state_start": attendee.event_id.start,
                    "bf_work_state_stop": attendee.event_id.stop,
                })

    @api.model
    def _bf_work_stamp_existing(self):
        """At installation: stamp the answers given before, from their last writer.

        The best there is for the past (a later recomputation of the line could
        have rewritten its writer); from now on every answer is stamped as it is
        given. Without this, an old answer would follow every later move.
        """
        self.env.flush_all()
        self.env.cr.execute("""
            UPDATE calendar_attendee a
               SET bf_work_state_by = CASE
                       WHEN a.write_uid = %s THEN 'odoo'
                       WHEN EXISTS (SELECT 1 FROM res_users u
                                     WHERE u.id = a.write_uid AND u.partner_id = a.partner_id) THEN 'self'
                       ELSE 'other' END,
                   bf_work_state_start = e.start,
                   bf_work_state_stop = e.stop
              FROM calendar_event e
             WHERE e.id = a.event_id AND a.state = 'accepted' AND a.bf_work_state_by IS NULL
        """, [SUPERUSER_ID])
        self.invalidate_model(list(STAMP_FIELDS))
        return self.env.cr.rowcount


class CalendarEvent(models.Model):
    _inherit = "calendar.event"

    def write(self, vals):
        res = super().write(vals)
        if {"start", "stop", "start_date", "stop_date", "duration", "allday"} & set(vals):
            self.attendee_ids._bf_work_follow_move()
        return res
