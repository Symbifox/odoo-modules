from odoo import _, api, models
from odoo.exceptions import AccessError


#: What a message says and who said it: written once. (The marks a reader puts on it, a star, are theirs.)
MESSAGE_CONTENT = {
    "body", "subject", "attachment_ids", "model", "res_id", "message_type", "subtype_id", "author_id",
    "author_guest_id", "email_from", "date", "tracking_value_ids", "partner_ids"}


class SchoolStaffThread(models.AbstractModel):
    """The thread of a report card or of a written communication belongs to the school's staff.

    The families are told by the email of the publication or of the communication and read the report card in
    the portal: they never follow the thread, and a message of the thread never goes to them. The thread
    carries the teachers' notes and is the trail of the state of a report card.
    """

    _name = "bf.school.staff.thread"
    _description = "Thread kept by the school's staff"
    _inherit = ["mail.thread"]

    #: The messages of a thread like this one are not edited or deleted (see `MailMessage`).
    _school_thread_kept = True

    @api.model
    def _school_outside_staff(self, partner_ids):
        """Those of these partners who are not a user of the school: a family, an adult with no authority."""
        ids = [p for p in getattr(partner_ids, "ids", partner_ids) or [] if isinstance(p, int)]
        if not ids:
            return self.env["res.partner"]
        partners = self.env["res.partner"].sudo().browse(ids)
        return partners.filtered(lambda p: not any(not user.share for user in p.user_ids))

    def _school_refuse_outside_staff(self, partner_ids):
        outside = self._school_outside_staff(partner_ids)
        if outside:
            # 🔴 A follower of the thread receives every note posted on it: the family does not follow, it reads
            # the report card in the portal and is told by the email of the publication.
            raise AccessError(_(
                "Only the school's staff follow or receive the messages of this thread: %s.",
                ", ".join(outside.mapped("name"))))

    def _message_subscribe(self, partner_ids=None, subtype_ids=None, customer_ids=None):
        if partner_ids:
            self._school_refuse_outside_staff(partner_ids)
            # 🔴 Reading is not following: a follower may post on the thread, which writing alone allows.
            self.check_access("write")
        return super()._message_subscribe(
            partner_ids=partner_ids, subtype_ids=subtype_ids, customer_ids=customer_ids)

    def message_post(self, **kwargs):
        self._school_refuse_outside_staff(kwargs.get("partner_ids"))
        return super().message_post(**kwargs)

    def _message_update_content(self, message, body, attachment_ids=None, partner_ids=None, strict=True, **kwargs):
        # 🔴 The chatter's own "edit" and "delete" of a message go through here, as the superuser: the messages of
        # these threads are their trail.
        raise AccessError(_("The messages of a report card or of a communication are kept as they were written."))


class MailMessage(models.Model):
    _inherit = "mail.message"

    def _school_check_kept(self):
        """The messages of the thread of a report card or a communication are its trail: nobody edits them."""
        if self.env.su:
            return
        for message in self.sudo():
            if message.model and message.model in self.env and getattr(
                    self.env[message.model], "_school_thread_kept", False):
                raise AccessError(_(
                    "The messages of a report card or of a communication are kept as they were written."))

    def write(self, vals):
        if MESSAGE_CONTENT & set(vals):
            self._school_check_kept()
        return super().write(vals)

    def unlink(self):
        self._school_check_kept()
        return super().unlink()
