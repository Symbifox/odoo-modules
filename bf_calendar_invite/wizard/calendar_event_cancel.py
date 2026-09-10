"""The dialog behind "Cancel" on a meeting.

It exists for one reason: cancelling carries a decision that cannot be
guessed, and the guess is expensive in both directions. Cancel silently and a
guest travels to a meeting that is not happening; notify by default and a
meeting moved between two colleagues sends a formal notice neither of them
wanted.

So the dialog asks, and it asks with the answer already visible: it names the
people who would be written to, so the choice is made against a list rather
than against an idea of one.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError


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
    recipient_ids = fields.Many2many(
        "res.partner",
        string="Guests to notify",
        compute="_compute_recipient_ids",
        help="Everyone invited except you. Guests with no email address are "
             "not listed, because they would not be written to.",
    )
    recipient_warning = fields.Char(compute="_compute_recipient_ids")
    blocker = fields.Char(compute="_compute_blocker")

    @api.depends("event_ids")
    def _compute_recipient_ids(self):
        for wizard in self:
            partners = self.env["res.partner"].browse()
            for event in wizard.event_ids:
                partners |= event._bf_cancellation_recipients()
            wizard.recipient_ids = partners
            # ⚠️ Said out loud rather than left to the empty list. An empty
            # widget reads as "not loaded yet", and the one case where it
            # matters — a meeting whose only other guest has no address — is
            # exactly the case where someone would tick the box and believe
            # the guest was told.
            wizard.recipient_warning = False if partners else _(
                "Nobody would be written to: this meeting has no guest with "
                "an email address besides you."
            )

    @api.depends("event_ids")
    def _compute_blocker(self):
        for wizard in self:
            blockers = [
                blocker for blocker in (
                    event._bf_cancel_blocker() for event in wizard.event_ids
                ) if blocker
            ]
            wizard.blocker = blockers[0] if blockers else False

    def action_apply(self):
        """Cancel, then close and let the calling view reload."""
        self.ensure_one()
        if not self.event_ids:
            raise UserError(_("There is no meeting to cancel."))
        self.event_ids._bf_cancel(
            reason=(self.reason or "").strip() or None,
            notify=self.notify,
        )
        return {"type": "ir.actions.act_window_close"}
