import re

from odoo import models

#: The portal and the school's public pages: the only links that get the language prefix
#: (/web/... is not a website route and would answer 404 with one).
_SCHOOL_PATHS = ("/my/", "/school/")


class MailTemplate(models.Model):
    _inherit = "mail.template"

    def send_mail(self, res_id, force_send=False, raise_exception=False, email_values=None,
                  email_layout_xmlid=False):
        """Every school email (the sender puts `school_lang` in the context) leaves branded and
        opens its pages in the recipient's language.

        🔴 A personal link without a language prefix opened in the visitor's BROWSER
        language: a French family with an English browser landed on an English page, and a
        visitor announcing no language fell on English (found in QA,
        2026-09-27). The prefix is added to the links before the email can leave, so a forced
        send waits for it.
        """
        lang = self.env.context.get("school_lang")
        if not lang:
            return super().send_mail(res_id, force_send=force_send, raise_exception=raise_exception,
                                     email_values=email_values, email_layout_xmlid=email_layout_xmlid)
        layout = email_layout_xmlid or self.env["bf.school"]._school_mail_layout()
        mail_id = super().send_mail(res_id, force_send=False, raise_exception=raise_exception,
                                    email_values=email_values, email_layout_xmlid=layout)
        mail = self.env["mail.mail"].sudo().browse(mail_id)
        prefix = self.env["bf.school"]._school_url_lang(lang)
        if prefix and mail.body_html:
            base = re.escape(self.env[self.model].browse(res_id).get_base_url().rstrip("/"))
            paths = "|".join(re.escape(p) for p in _SCHOOL_PATHS)
            mail.body_html = re.sub(r'(href=["\'])(%s)(%s)' % (base, paths),
                                    lambda m: m.group(1) + m.group(2) + prefix + m.group(3), mail.body_html)
        if force_send:
            mail.send(raise_exception=raise_exception)
        return mail_id
