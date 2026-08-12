"""Post-send hook: stop the share link from living on in the chatter.

``mail.template.send_mail`` stores the rendered body on the record (mail.mail
_inherits mail.message), so the transfer's own chatter kept a readable copy of
the tokenized link — bypassing both the manager-only ``token`` field and the
journaled reveal wizard. The body has to hold the real link until the queue has
actually sent it; the moment it has, we blot the token out.

Scoped to secure.transfer mails: every other model's mail is untouched.
"""
from odoo import models


class MailMail(models.Model):
    _inherit = "mail.mail"

    def _postprocess_sent_message(self, success_pids, failure_reason=False,
                                  failure_type=None):
        res = super()._postprocess_sent_message(
            success_pids, failure_reason=failure_reason,
            failure_type=failure_type,
        )
        # super() unlinks the auto_delete mails, so re-check existence before
        # reading anything back.
        sent = self.exists().filtered(
            lambda m: m.model == "secure.transfer" and m.res_id
            and m.state == "sent"
        )
        if sent:
            self.env["secure.transfer"].sudo().browse(
                sorted(set(sent.mapped("res_id")))
            ).exists()._redact_chatter_links()
        return res
