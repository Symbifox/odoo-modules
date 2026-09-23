from odoo import models


class MailActivityMixin(models.AbstractModel):
    _inherit = "mail.activity.mixin"

    def _bf_note_activities_of_archived(self, records):
        # active_test=True: project.project archives its tasks with
        # active_test=False, which would also return done keep_done activities.
        activities = self.env["mail.activity"].sudo().with_context(active_test=True).search(
            [("res_model", "=", self._name), ("res_id", "in", records.ids)]
        )
        activities._bf_post_cancel_note(
            reason=activities._bf_archive_reason(), automatic=True
        )

    def write(self, vals):
        # Odoo deletes the activities of an archived record without a trace
        # (mail.activity.mixin.write). Leave the cancellation note first; the
        # parent write then unlinks the same activities.
        if "active" in vals and vals["active"] is False:
            self._bf_note_activities_of_archived(self)
        return super().write(vals)

    def toggle_active(self):
        # action_archive goes through toggle_active, which unlinks the
        # activities BEFORE calling write: the write override above would
        # find nothing. This is the path of "Archive" and of a lost CRM lead.
        to_deactivate = self.filtered(lambda rec: rec[rec._active_name])
        if to_deactivate:
            self._bf_note_activities_of_archived(to_deactivate)
        return super().toggle_active()
