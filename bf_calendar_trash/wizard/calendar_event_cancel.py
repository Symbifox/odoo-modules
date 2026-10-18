"""« Move to trash » on the cancellation dialog.

The gesture this answers: a meeting
cancelled, then archived by hand nine seconds later, then back in the agenda
thirteen seconds after that. Cancelling and throwing away are one decision
when the meeting is not being moved but dropped.
"""

from odoo import fields, models


class BfCalendarEventCancel(models.TransientModel):
    _inherit = "bf.calendar.event.cancel"

    bf_trash = fields.Boolean(
        string="Move to trash",
        default=False,
        help="Once cancelled, the meeting leaves the agenda and goes to the "
             "trash (Calendar > Trash), from where it can be restored. A "
             "meeting kept in Nextcloud leaves it too. On a series, the same "
             "dates as the cancellation.",
    )

    def action_apply(self):
        self.ensure_one()
        if not self.bf_trash:
            return super().action_apply()
        # Read before cancelling: the scope drops what is already cancelled.
        targets = self._targets()
        result = super().action_apply()
        if not targets:
            return result
        first = targets[0]
        if self.scope == "self_only" or not first.recurrence_id:
            targets._bf_trash("self_only")
        else:
            # "This one and the following ones", and "the whole series" as this
            # dialog means it (every date not over yet): both cut the series at
            # its first date reached. Core stops the recurrence there.
            first.with_context(bf_trash_door=True).action_mass_archive("future_events")
        return result
