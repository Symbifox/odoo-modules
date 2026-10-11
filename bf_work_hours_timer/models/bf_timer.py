from datetime import timedelta

from odoo import api, fields, models

# A stretch longer than this is a timer left running, not work: it is not kept at
# all (cutting it at 12 h still counted 12 h of a forgotten timer; timers left open
# for days happen).
SEGMENT_MAX = timedelta(hours=12)
# What decides when a timer ran. Changed by anyone but its owner, the timer no
# longer says when its owner worked (an approver may edit any timer).
TIMING_FIELDS = {"start_time", "first_start", "is_active", "is_paused", "accumulated_seconds", "paused_at"}


class BfTimer(models.Model):
    """Keep each segment a timer ran, start and end, before the timer forgets it.

    The timer folds a segment into ``accumulated_seconds`` at a pause and at
    the stop, and the timesheet line keeps only a date and a duration: the
    time of day is lost. For the people who turned "Propose my hours" on, the
    segment is kept as a trace, ended at the time of the gesture: the phone
    pauses offline and sends the real moment (``paused_at``) later.
    """

    _inherit = "bf.timer"

    bf_work_untrusted = fields.Boolean(
        string="Changed by someone else", readonly=True, copy=True,
        help="Its timing was changed by someone other than its owner: it no longer counts as a trace.")

    def _bf_work_keep_segment(self, now=None):
        now = now or fields.Datetime.now()
        active = self.env["bf.work.consent"].sudo()._bf_active_user_ids("hours")
        Signal = self.env["bf.work.signal"].sudo()
        for timer in self:
            if timer.is_active and not timer.is_paused and timer.start_time and timer.user_id.id in active \
                    and timer.start_time < now <= timer.start_time + SEGMENT_MAX and not timer.bf_work_untrusted:
                Signal.create({"user_id": timer.user_id.id, "source": "timer", "bf_timer_id": timer.id,
                               "date_start": timer.start_time, "date_stop": now})

    @api.model
    def pause_timer(self, timer_id):
        timer = self.browse(timer_id)
        if timer.exists() and timer.user_id.id == self.env.uid:
            timer._bf_work_keep_segment()
        return super().pause_timer(timer_id)

    def _stop_and_freeze(self):
        self._bf_work_keep_segment()
        return super()._stop_and_freeze()

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            # A caller may not clear the marker (a copy carries True and keeps it).
            vals_list = [{k: v for k, v in vals.items() if k != "bf_work_untrusted" or v} for vals in vals_list]
        timers = super().create(vals_list)
        if not self.env.su:
            # 🔴 A timer made by someone for someone else (or copied from one) does
            # not say when its owner worked.
            given = timers.filtered(lambda t: t.user_id.id != self.env.uid)
            if given:
                super(BfTimer, given.sudo()).write({"bf_work_untrusted": True})
        return timers

    def write(self, vals):
        vals = {k: v for k, v in vals.items() if k != "bf_work_untrusted" or self.env.su}
        if not self.env.su and (TIMING_FIELDS | {"user_id"}) & set(vals):
            # Judged before AND after: a timer timed by its owner, then handed to
            # someone else (user_id), is not the new owner's.
            others = self.filtered(lambda t: t.user_id.id != self.env.uid) | (self if "user_id" in vals else self.browse())
            if others:
                super(BfTimer, others.sudo()).write({"bf_work_untrusted": True})
        res = super().write(vals)
        if vals.get("paused_at"):
            # The real moment of a pause made offline: the segment kept at the
            # sync ends there, not at the time the phone came back online.
            at = fields.Datetime.to_datetime(vals["paused_at"])
            Signal = self.env["bf.work.signal"].sudo()
            for timer in self:
                if not self.env.su and timer.user_id.id != self.env.uid:
                    continue  # an approver rewriting someone's pause does not move their trace
                last = Signal.search([("bf_timer_id", "=", timer.id)], order="date_start desc", limit=1)
                if last and last.date_stop > at:
                    last.write({"date_stop": max(last.date_start, at)})
        return res
