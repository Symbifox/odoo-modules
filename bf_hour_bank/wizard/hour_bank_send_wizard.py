import base64

from markupsafe import Markup

from odoo import api, fields, models, _
from odoo.exceptions import UserError

# Blue Fox branded email wrapper. Colors substituted at render time from
# company.report_brand_primary / report_brand_dark (bf_lexend). Placeholders:
#   {primary}      → company.report_brand_primary
#   {dark}         → company.report_brand_dark
#   {company_name} → company.name
#   {content}      → inner message HTML
_BRANDED_WRAPPER = """\
<table role="presentation" cellpadding="0" cellspacing="0" border="0" \
width="100%" style="background-color:#F8FAFC;">\
<tbody><tr><td align="center" style="padding:24px;">\
<table cellspacing="0" cellpadding="0" border="0" width="100%" \
align="center" role="presentation"><tbody><tr><td><br/>\
<table role="presentation" cellpadding="0" cellspacing="0" border="0" \
width="600" style="width:600px;max-width:600px;margin:0 auto;\
background-color:#ffffff;border-radius:12px;border:1px solid #e5e7eb;\
border-collapse:collapse;"><tbody>\
<tr><td style="background-color:{dark};padding:16px 24px;\
border-radius:12px 12px 0 0;">\
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" \
border="0"><tbody><tr>\
<td align="left" style="color:#FFFFFF;font-family:'Lexend','Segoe UI',\
Arial,sans-serif;font-size:16px;font-weight:600;">\
<a href="{company_website}" style="text-decoration:none;">\
<img src="{logo_url}" alt="{company_name}" style="height:48px;width:auto;\
display:block;border:0;"/></a></td>\
<td align="right" style="color:#E6EDF3;font-family:'Lexend','Segoe UI',\
Arial,sans-serif;font-size:22px;font-weight:800;letter-spacing:0.2px;">\
Banque d'heures</td>\
</tr></tbody></table></td></tr>\
<tr><td style="height:4px;line-height:4px;background-color:{primary};">\
&nbsp;</td></tr>\
<tr><td style="padding:24px;font-family:'Lexend','Segoe UI',Arial,\
sans-serif;">\
{content}\
<p style="font-size:13px;line-height:20px;color:#6B7280;\
margin:16px 0 0 0;">\
Pour toute question, contactez-nous &agrave; \
<a href="mailto:{company_email}" \
style="color:{primary};text-decoration:none;">\
{company_email}</a> ou appelez le \
<a href="tel:{company_phone}" style="color:{primary};text-decoration:none;">\
{company_phone}</a>.</p>\
</td></tr>\
<tr><td style="height:1px;line-height:1px;background-color:#E5E7EB;">\
&nbsp;</td></tr>\
<tr><td style="padding:16px 24px 24px 24px;background-color:#FFFFFF;\
border-radius:0 0 12px 12px;">\
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" \
border="0"><tbody><tr>\
<td style="font-family:'Lexend','Segoe UI',Arial,sans-serif;\
font-size:12px;color:#6B7280;">\
<strong style="color:{dark};">Banque d'heures par {company_name}</strong><br/>\
Solutions &eacute;thiques et souveraines pour vos donn&eacute;es.</td>\
<td align="right" style="font-family:'Lexend','Segoe UI',Arial,sans-serif;\
font-size:12px;color:#9CA3AF;">\
<a href="{privacy_url}" \
style="color:#9CA3AF;text-decoration:underline;">\
Politique de confidentialit&eacute;</a>\
<span style="color:#9CA3AF;"> | </span>\
<a href="{terms_url}" \
style="color:#9CA3AF;text-decoration:underline;">Conditions</a>\
</td></tr></tbody></table></td></tr>\
</tbody></table>\
<table role="presentation" cellpadding="0" cellspacing="0" border="0" \
width="600" style="width:600px;max-width:600px;margin:12px auto 0;">\
<tbody><tr>\
<td style="height:3px;line-height:3px;background-color:{primary};\
width:50%;">&nbsp;</td>\
<td style="height:3px;line-height:3px;background-color:{dark};\
width:50%;">&nbsp;</td>\
</tr></tbody></table>\
</td></tr></tbody></table>\
</td></tr></tbody></table>"""


class HourBankSendWizard(models.TransientModel):
    _name = 'hour.bank.send.wizard'
    _description = "Envoi du rapport de banque d'heures"

    hour_bank_id = fields.Many2one(
        'hour.bank.client', required=True, string="Banque d'heures",
    )
    recipient_ids = fields.Many2many(
        'res.partner', string="Destinataires",
    )
    subject = fields.Char(string="Sujet")
    body = fields.Html(string="Corps du message")
    preview_url = fields.Char(readonly=True)

    @api.onchange('hour_bank_id')
    def _onchange_hour_bank_id(self):
        if self.hour_bank_id:
            self.recipient_ids = self.hour_bank_id.recipient_ids
            self.subject = "État des banques d'heures — %s" % (
                self.hour_bank_id.partner_id.name,
            )
            self.body = (
                '<p style="font-size:16px;line-height:26px;color:#374151;'
                'margin:0 0 16px 0;">Bonjour,</p>'
                '<p style="font-size:16px;line-height:26px;color:#374151;'
                'margin:0 0 20px 0;">Veuillez trouver ci-joint '
                "l'&#233;tat des banques d'heures pour "
                "<strong>%s</strong>.</p>"
                '<p style="font-size:16px;line-height:26px;color:#374151;'
                'margin:0 0 20px 0;">Les fichiers PDF et Excel sont '
                "en pi&#232;ces jointes.</p>"
                '<p style="font-size:16px;line-height:26px;color:#374151;'
                'margin:0;">Cordialement,<br/>%s</p>'
            ) % (
                self.hour_bank_id.partner_id.name,
                self.env.user.name,
            )

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for rec in records:
            if rec.hour_bank_id and not rec.recipient_ids:
                rec._onchange_hour_bank_id()
        return records

    def _wrap_branded_body(self, inner_html):
        """Wrap inner message HTML with the company-branded email layout.
        Colors pulled from bf_lexend company fields; falls back to canonical hex.
        Logo, website, and policy/terms URLs are tenant-aware so each company
        in the database sends emails with its own visual identity."""
        company = self.env.company
        website = company.website or 'https://bluefoxconsultant.com'
        return Markup(_BRANDED_WRAPPER.format(
            primary=company.report_brand_primary or '#714B67',
            dark=company.report_brand_dark or '#212529',
            company_name=company.name or 'Blue Fox',
            company_email=company.email or 'service@example.com',
            company_phone=company.phone or '555-555-5555',
            company_website=website,
            logo_url='/web/image/res.company/%d/logo' % company.id,
            privacy_url=website.rstrip('/') + '/r/politique-de-confidentialite',
            terms_url=website.rstrip('/') + '/r/termes-et-conditions',
            content=str(inner_html or ''),
        ))

    def action_preview_pdf(self):
        """Generate PDF preview and re-open wizard with download link."""
        self.ensure_one()
        hb = self.hour_bank_id
        partner_name = hb.partner_id.name.replace(' ', '_')
        date_str = fields.Date.today().isoformat()

        pdf_data = hb._get_pdf_binary()
        att = self.env['ir.attachment'].create({
            'name': "Apercu_Banque_heures_%s_%s.pdf" % (partner_name, date_str),
            'type': 'binary',
            'datas': base64.b64encode(pdf_data),
            'mimetype': 'application/pdf',
            'res_model': hb._name,
            'res_id': hb.id,
        })
        # Store URL on wizard and re-open it (keeps wizard alive)
        self.preview_url = '/web/content/%d?download=false' % att.id
        return {
            'type': 'ir.actions.act_window',
            'name': _("Envoyer le rapport"),
            'res_model': self._name,
            'res_id': self.id,
            'views': [[False, 'form']],
            'target': 'new',
        }

    def action_send(self):
        """Generate PDF + Excel, attach them, and send branded email."""
        self.ensure_one()
        hb = self.hour_bank_id

        if not self.recipient_ids:
            raise UserError(_("Veuillez sélectionner au moins un destinataire."))

        # Generate attachments
        partner_name = hb.partner_id.name.replace(' ', '_')
        date_str = fields.Date.today().isoformat()

        # PDF
        pdf_data = hb._get_pdf_binary()
        pdf_att = self.env['ir.attachment'].create({
            'name': "Banque_heures_%s_%s.pdf" % (partner_name, date_str),
            'type': 'binary',
            'datas': base64.b64encode(pdf_data),
            'mimetype': 'application/pdf',
            'res_model': hb._name,
            'res_id': hb.id,
        })

        # Excel
        xlsx_data = hb._generate_xlsx_binary()
        xlsx_att = self.env['ir.attachment'].create({
            'name': "Banque_heures_%s_%s.xlsx" % (partner_name, date_str),
            'type': 'binary',
            'datas': base64.b64encode(xlsx_data),
            'mimetype': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            'res_model': hb._name,
            'res_id': hb.id,
        })

        # Wrap body with branded layout
        body_html = self._wrap_branded_body(self.body or '')

        # Send email to each recipient individually
        for partner in self.recipient_ids:
            if not partner.email:
                continue
            mail_values = {
                'subject': self.subject,
                'body_html': body_html,
                'email_from': self.env.user.email_formatted,
                # recipient_ids ONLY — never also set email_to to the same
                # person, or Odoo sends two identical copies per recipient.
                'recipient_ids': [(4, partner.id)],
                'attachment_ids': [(6, 0, [pdf_att.id, xlsx_att.id])],
            }
            mail = self.env['mail.mail'].sudo().create(mail_values)
            mail.send()

        # Update last report date
        hb.write({'last_report_date': fields.Datetime.now()})

        # Post a note on the chatter
        recipient_names = ', '.join(self.recipient_ids.mapped('name'))
        hb.message_post(
            body=_("Rapport envoyé à %s") % recipient_names,
            message_type='comment',
            subtype_xmlid='mail.mt_note',
            attachment_ids=[pdf_att.id, xlsx_att.id],
        )

        return {'type': 'ir.actions.act_window_close'}
