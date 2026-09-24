"""The dialog that proposes telling the guests a meeting moved.

It exists for the same reason the cancellation dialog does, and for one more.

The same reason: the decision cannot be guessed. A meeting moved between two
colleagues in a call they were both on does not need a formal notice; the same
move, made a week ahead on a client's meeting, absolutely does.

The one more: this dialog is also where the person finds out that a change is
owed at all. A move made by dragging a block across the grid is a single
gesture with no dialog behind it, so the meeting carries a banner afterwards
and the banner opens this. Whoever ignores it is not the last line: the
meetings still owing a notice are what the daily watchdog looks for.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class BfCalendarEventChangeNotice(models.TransientModel):
    _name = "bf.calendar.event.change.notice"
    _description = "Tell the guests a meeting moved"

    event_ids = fields.Many2many(
        "calendar.event",
        string="Meetings",
        required=True,
    )
    change_summary = fields.Html(
        string="What changed",
        compute="_compute_change_summary",
        help="Read from the meeting itself: the values the guests hold, "
             "against the values it holds now.",
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
                partners |= event._bf_notice_recipients()
            wizard.recipient_ids = partners
            # ⚠️ Said out loud rather than left to the empty list. An empty
            # widget reads as "not loaded yet", and the one case where it
            # matters, a meeting whose only other guest has no address, is
            # exactly the case where someone would press Send and believe the
            # guest was told.
            wizard.recipient_warning = False if partners else _(
                "Nobody would be written to: this meeting has no guest with an "
                "email address other than you."
            )

    @api.depends("event_ids")
    def _compute_change_summary(self):
        for wizard in self:
            blocks = []
            for event in wizard.event_ids:
                lines = event.bf_change_lines()
                if not lines:
                    continue
                rows = "".join(
                    "<tr><td class='pe-3 text-muted'>%s</td>"
                    "<td class='pe-3 text-decoration-line-through'>%s</td>"
                    "<td class='fw-bold'>%s</td></tr>"
                    % (line["label"], line["before"], line["after"])
                    for line in lines
                )
                blocks.append(
                    "<div class='mb-2'><div class='fw-bold'>%s</div>"
                    "<table>%s</table></div>" % (event.name or "", rows)
                )
            wizard.change_summary = "".join(blocks) or False

    @api.depends("event_ids")
    def _compute_blocker(self):
        for wizard in self:
            blocker = False
            for event in wizard.event_ids:
                if not event._bf_change_notice_due():
                    blocker = _(
                        "There is nothing to tell the guests about this "
                        "meeting: either it has not changed since they were "
                        "last written to, it has already happened, or it is "
                        "cancelled."
                    )
                    break
            wizard.blocker = blocker

    def action_apply(self):
        self.ensure_one()
        if self.blocker:
            raise UserError(self.blocker)
        self.event_ids._bf_send_change_notice()
        return {"type": "ir.actions.act_window_close"}
