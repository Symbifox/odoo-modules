from odoo import models


class MailActivity(models.Model):
    _inherit = "mail.activity"

    def unlink_w_meeting(self):
        """"Delete" on an activity and its meeting: the meeting goes to the trash."""
        # A meeting already in the trash stays there: deleting an activity is
        # no way to delete it for good, unasked.
        events = self.mapped("calendar_event_id").filtered("active")
        res = self.unlink()
        events.with_context(bf_trash_door=True).unlink()
        return res
