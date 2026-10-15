"""The dialog behind "Cancel" on a meeting.

It exists for one reason: cancelling carries a decision that cannot be
guessed, and the guess is expensive in both directions. Cancel silently and a
guest travels to a meeting that is not happening; notify by default and a
meeting moved between two colleagues sends a formal notice neither of them
wanted.

So the dialog asks, and it asks with the answer already visible: it names the
people who would be written to, so the choice is made against a list rather
than against an idea of one.

On a recurring meeting it asks one more thing, the one core asks of anyone who
edits a series: this occurrence, this one and the following ones, or the whole
series. It asks it here, with the dates it would reach written out, because a
popover has no room to show them and a wrong answer cancels a series by email.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import format_date


class BfCalendarEventCancel(models.TransientModel):
    _name = "bf.calendar.event.cancel"
    _description = "Cancel a meeting"

    event_ids = fields.Many2many(
        "calendar.event",
        string="Meetings",
        required=True,
    )
    notify = fields.Boolean(
        string="Send a cancellation notice",
        default=False,
        help="Write to the guests to tell them the meeting is off. The "
             "message carries a .ics that removes the entry from their own "
             "calendar.",
    )
    reason = fields.Text(
        string="Reason",
        help="Optional. Included in the notice when one is sent; kept on the "
             "meeting either way.",
    )
    has_series = fields.Boolean(compute="_compute_has_series")
    scope = fields.Selection(
        [
            ("self_only", "This meeting only"),
            ("future_events", "This meeting and the following ones"),
            ("all_events", "The whole series"),
        ],
        string="Which meetings",
        default="self_only",
        required=True,
        help="Only asked on a recurring meeting. \"The whole series\" means "
             "every occurrence that has not ended yet: the ones already held "
             "are left as they were.",
    )
    target_ids = fields.Many2many(
        "calendar.event",
        string="Meetings to cancel",
        compute="_compute_target_ids",
        help="What the chosen scope reaches. Occurrences already cancelled "
             "are not listed: nobody is told twice.",
    )
    target_summary = fields.Char(compute="_compute_target_ids")
    recipient_ids = fields.Many2many(
        "res.partner",
        string="Guests to notify",
        compute="_compute_recipient_ids",
        help="Everyone invited except you. Guests with no email address are "
             "not listed, because they would not be written to.",
    )
    recipient_warning = fields.Char(compute="_compute_recipient_ids")

    @api.depends("event_ids")
    def _compute_has_series(self):
        for wizard in self:
            wizard.has_series = any(
                event.recurrence_id for event in wizard.event_ids._origin
            )

    def _targets(self):
        """The live meetings the chosen scope reaches."""
        self.ensure_one()
        targets = self.env["calendar.event"]
        for event in self.event_ids._origin:
            targets |= event._bf_series_scope(self.scope)
        return targets.sorted("start")

    @api.depends("event_ids", "scope")
    def _compute_target_ids(self):
        for wizard in self:
            targets = wizard._targets()
            wizard.target_ids = targets
            if len(targets) < 2:
                wizard.target_summary = False
                continue
            first, last = (
                fields.Datetime.context_timestamp(wizard, targets[0].start).date(),
                fields.Datetime.context_timestamp(wizard, targets[-1].start).date(),
            )
            wizard.target_summary = _(
                "%(count)s meetings will be cancelled, from %(first)s to %(last)s.",
                count=len(targets),
                first=format_date(wizard.env, first),
                last=format_date(wizard.env, last),
            )

    @api.depends("event_ids", "scope")
    def _compute_recipient_ids(self):
        for wizard in self:
            partners = self.env["res.partner"].browse()
            targets = wizard._targets()
            for event in targets:
                partners |= event._bf_cancellation_recipients()
            wizard.recipient_ids = partners
            # ⚠️ Said out loud rather than left to the empty list. An empty
            # widget reads as "not loaded yet", and the one case where it
            # matters — a meeting whose only other guest has no address — is
            # exactly the case where someone would tick the box and believe
            # the guest was told.
            if partners:
                wizard.recipient_warning = False
            elif not targets:
                # Everything the choice reaches is already cancelled or over: say
                # that, not "no guest" (which would send the reader looking for one).
                wizard.recipient_warning = _(
                    "Nothing would change: every meeting this choice reaches "
                    "is already cancelled or over."
                )
            else:
                wizard.recipient_warning = _(
                    "Nobody would be written to: this meeting has no guest with "
                    "an email address besides you."
                )

    def action_apply(self):
        """Cancel, then close and let the calling view reload."""
        self.ensure_one()
        if not self.event_ids:
            raise UserError(_("There is no meeting to cancel."))
        # An empty set is not an error: everything the scope reaches is already
        # cancelled, which is what a double click on the button looks like.
        self._targets()._bf_cancel(
            reason=(self.reason or "").strip() or None,
            notify=self.notify,
        )
        return {"type": "ir.actions.act_window_close"}
