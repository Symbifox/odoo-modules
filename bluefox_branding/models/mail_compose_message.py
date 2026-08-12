from odoo import models


class MailComposeMessage(models.TransientModel):
    _inherit = 'mail.compose.message'

    # Map standard Odoo layouts → Blue Fox layouts
    _BF_LAYOUT_MAP = {
        'mail.mail_notification_layout':
            'bluefox_branding.bf_mail_layout',
        'mail.mail_notification_layout_with_responsible_signature':
            'bluefox_branding.bf_mail_layout_with_signature',
    }

    def action_send_mail(self):
        """Swap standard Odoo email layouts with Blue Fox branded layouts.

        Only affects emails sent through the composer wizard (invoices,
        quotes, contracts). Does NOT affect chatter notifications or
        internal messages, which go through _notify_thread_by_email
        and use mail.mail_notification_layout directly.
        """
        for wizard in self:
            bf_layout = self._BF_LAYOUT_MAP.get(wizard.email_layout_xmlid)
            if bf_layout:
                wizard.email_layout_xmlid = bf_layout
        return super().action_send_mail()
