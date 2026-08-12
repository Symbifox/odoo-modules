from odoo import models


class MailScheduledMessage(models.Model):
    _inherit = "mail.scheduled.message"

    def post_message(self):
        return super(
            MailScheduledMessage,
            self.with_context(mail_notify_force_send=True),
        ).post_message()
