import base64

from markupsafe import Markup, escape

from odoo import _, api, fields, models
from odoo.exceptions import UserError

# Branded email wrapper. Colors substituted at render time from the letter's
# company (report_brand_primary / report_brand_dark, provided by bf_lexend).
_BRANDED_WRAPPER = """\
<table role="presentation" cellpadding="0" cellspacing="0" border="0" \
width="100%" style="background-color:#F8FAFC;"><tbody><tr>\
<td align="center" style="padding:24px;">\
<table role="presentation" cellpadding="0" cellspacing="0" border="0" \
width="600" style="width:600px;max-width:600px;margin:0 auto;\
background-color:#ffffff;border-radius:12px;border:1px solid #e5e7eb;\
border-collapse:collapse;"><tbody>\
<tr><td style="background-color:{dark};padding:16px 24px;\
border-radius:12px 12px 0 0;">\
<img src="{logo_url}" alt="{company_name}" style="height:44px;width:auto;\
display:block;border:0;"/></td></tr>\
<tr><td style="height:4px;line-height:4px;background-color:{primary};">\
&nbsp;</td></tr>\
<tr><td style="padding:24px;font-family:'Lexend','Segoe UI',Arial,\
sans-serif;font-size:15px;line-height:24px;color:#374151;">{content}</td></tr>\
<tr><td style="padding:16px 24px 24px 24px;border-top:1px solid #E5E7EB;\
font-family:'Lexend','Segoe UI',Arial,sans-serif;font-size:12px;\
color:#6B7280;"><strong style="color:{dark};">{company_name}</strong>\
</td></tr></tbody></table></td></tr></tbody></table>"""


class LetterSendWizard(models.TransientModel):
    _name = "letter.send.wizard"
    _description = "Send letter by email"

    letter_id = fields.Many2one(
        "letter.document", string="Letter", required=True,
    )
    recipient_ids = fields.Many2many("res.partner", string="Recipients")
    subject = fields.Char(string="Email subject")
    body = fields.Html(string="Message")
    attach_pdf = fields.Boolean(string="Attach PDF", default=True)
    preview_url = fields.Char(readonly=True)

    @api.onchange("letter_id")
    def _onchange_letter_id(self):
        letter = self.letter_id
        if not letter:
            return
        if letter.partner_id:
            self.recipient_ids = letter.partner_id
        self.subject = letter.name
        # The recipient reads this message: in the letter's language, like its title.
        lu = self.with_context(lang=letter._bf_reader_lang(letter.partner_id, letter.lang))
        self.body = Markup(lu.env._(
            "<p>Hello,</p><p>Please find attached the letter "
            "<strong>%s</strong>.</p><p>Best regards,<br/>%s</p>"
        )) % (letter.name or "", self.env.user.name)

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for rec in records:
            if rec.letter_id and not rec.recipient_ids:
                rec._onchange_letter_id()
        return records

    def _wrap_branded_body(self, inner_html):
        company = self.letter_id.company_id or self.env.company
        return Markup(
            _BRANDED_WRAPPER.format(
                primary=escape(company.report_brand_primary or "#714B67"),
                dark=escape(company.report_brand_dark or "#212529"),
                company_name=escape(company.name or ""),
                logo_url="/web/image/res.company/%d/logo" % company.id,
                content=str(inner_html or ""),
            )
        )

    def _build_pdf_attachment(self):
        letter = self.letter_id
        pdf_data = letter._get_pdf_binary()
        return self.env["ir.attachment"].create(
            {
                "name": "%s.pdf" % (letter.reference or "letter"),
                "type": "binary",
                "datas": base64.b64encode(pdf_data),
                "mimetype": "application/pdf",
                "res_model": letter._name,
                "res_id": letter.id,
            }
        )

    def action_preview_pdf(self):
        self.ensure_one()
        attachment = self._build_pdf_attachment()
        self.preview_url = "/web/content/%d?download=false" % attachment.id
        return {
            "type": "ir.actions.act_window",
            "name": _("Send the letter"),
            "res_model": self._name,
            "res_id": self.id,
            "views": [[False, "form"]],
            "target": "new",
        }

    def action_send(self):
        self.ensure_one()
        if not self.recipient_ids:
            raise UserError(_("Select at least one recipient."))
        letter = self.letter_id
        attachment_ids = []
        if self.attach_pdf:
            attachment_ids = [self._build_pdf_attachment().id]
        body_html = self._wrap_branded_body(self.body or "")
        sent_to = []
        for partner in self.recipient_ids:
            email = partner.email_formatted or partner.email
            if not email:
                continue
            mail = (
                self.env["mail.mail"]
                .sudo()
                .create(
                    {
                        "subject": self.subject or letter.name,
                        "body_html": body_html,
                        "email_from": self.env.user.email_formatted,
                        "email_to": email,
                        "recipient_ids": [(4, partner.id)],
                        "attachment_ids": [(6, 0, attachment_ids)]
                        if attachment_ids
                        else False,
                    }
                )
            )
            mail.send()
            sent_to.append(partner.name)
        if not sent_to:
            raise UserError(
                _("None of the selected recipients has an email address.")
            )
        letter.action_mark_sent()
        letter.message_post(
            body=_("Letter emailed to %s.") % ", ".join(sent_to),
            message_type="comment",
            subtype_xmlid="mail.mt_note",
            attachment_ids=attachment_ids,
        )
        return {"type": "ir.actions.act_window_close"}
