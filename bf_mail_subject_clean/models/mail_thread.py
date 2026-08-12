from odoo import api, models

from .common import normalize_reply_subject


class MailThread(models.AbstractModel):
    _inherit = 'mail.thread'

    @api.returns('mail.message', lambda value: value.id)
    def message_post(self, *, subject=None, **kwargs):
        return super().message_post(
            subject=normalize_reply_subject(subject),
            **kwargs,
        )
