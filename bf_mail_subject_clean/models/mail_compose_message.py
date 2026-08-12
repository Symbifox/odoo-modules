from odoo import models

from .common import normalize_reply_subject


class MailComposeMessage(models.TransientModel):
    _inherit = 'mail.compose.message'

    def _compute_subject(self):
        super()._compute_subject()
        for composer in self:
            cleaned = normalize_reply_subject(composer.subject)
            if cleaned != composer.subject:
                composer.subject = cleaned
