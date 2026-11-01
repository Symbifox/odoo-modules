from odoo import models


class MailTemplate(models.Model):
    _inherit = "mail.template"

    def send_mail(self, res_id, force_send=False, raise_exception=False, email_values=None,
                  email_layout_xmlid=False):
        # survey._mark_done() sends the certification email with the light
        # notification layout forced in the call, which silences the layout
        # configured ON the template (our branded one). The repair is as
        # narrow as the bug: only templates on survey.user_input, only when
        # they declare their own layout, and only when the caller forces the
        # light one. Every other template keeps today's behaviour. Same
        # signature as the core; arguments go to super() untouched.
        if (self.model == "survey.user_input"
                and self.email_layout_xmlid
                and email_layout_xmlid == "mail.mail_notification_light"):
            email_layout_xmlid = self.email_layout_xmlid
        return super().send_mail(
            res_id, force_send=force_send, raise_exception=raise_exception,
            email_values=email_values, email_layout_xmlid=email_layout_xmlid)
