from collections import defaultdict

from odoo import _, models


class MailActivity(models.Model):
    _inherit = "mail.activity"

    def action_cancel_with_note(self, reason=False):
        """Cancel the activities and leave a note in the chatter of their record.

        Public on purpose: the chatter calls it by RPC. Access is checked before
        anything is posted, because the note is posted on the record in sudo.
        """
        self.check_access("unlink")
        active = self.filtered("active")
        active._bf_post_cancel_note(reason=reason)
        active.unlink()
        return True

    def _bf_post_cancel_note(self, reason=False, automatic=False):
        """Post the cancellation note, modelled on ``_action_done``.

        The activity's attachments move to the note so they survive the unlink.
        Nothing here unlinks the activity, creates the chained next activity or
        goes through ``_action_done``: a cancellation is not a completion.
        """
        # An archived activity is a done keep_done one: never note it as cancelled.
        return self.filtered("active")._bf_post_cancel_note_active(reason, automatic)

    def _bf_post_cancel_note_active(self, reason, automatic):
        messages = self.env["mail.message"]
        if not self:
            return messages
        attachments = self.env["ir.attachment"].sudo().search_read(
            [("res_model", "=", self._name), ("res_id", "in", self.ids)],
            ["id", "res_id"],
        )
        activity_attachments = defaultdict(list)
        for attachment in attachments:
            activity_attachments[attachment["res_id"]].append(attachment["id"])

        for model, activity_data in self._classify_by_model().items():
            records_sudo = self.env[model].sudo().browse(activity_data["record_ids"])
            existing = records_sudo.exists()
            for record_sudo, activity in zip(records_sudo, activity_data["activities"]):
                if record_sudo not in existing or not hasattr(record_sudo, "message_post_with_source"):
                    continue
                message = record_sudo.message_post_with_source(
                    "bf_activity_cancel_note.message_activity_cancelled",
                    author_id=self.env.user.partner_id.id,
                    render_values={
                        "activity": activity,
                        "reason": reason,
                        "automatic": automatic,
                        "display_assignee": activity.user_id != self.env.user,
                    },
                    mail_activity_type_id=activity.activity_type_id.id,
                    subtype_xmlid="mail.mt_activities",
                )
                if activity_attachments[activity.id]:
                    moved = self.env["ir.attachment"].sudo().browse(activity_attachments[activity.id])
                    moved.write({"res_id": message.id, "res_model": message._name})
                    message.sudo().attachment_ids = moved
                messages += message
        return messages

    def _bf_archive_reason(self):
        return _("The record was archived.")
