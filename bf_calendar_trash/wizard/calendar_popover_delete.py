"""The popover's delete dialog, on a series.

On a database with neither Google nor Microsoft calendar,
core's popover does not archive: "Delete" opens this dialog, which erases
(`unlink`, `action_mass_deletion`). It was the commonest way to delete a
series, and it went past the trash.
"""

from odoo import models

SCOPES = {"one": "self_only", "next": "future_events", "all": "all_events"}


class CalendarPopoverDeleteWizard(models.TransientModel):
    _inherit = "calendar.popover.delete.wizard"

    def close(self):
        if not self.record or self.delete not in SCOPES:
            return super().close()
        self.record.with_context(bf_trash_door=True).action_mass_archive(SCOPES[self.delete])
