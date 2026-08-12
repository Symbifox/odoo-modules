import base64

from markupsafe import Markup

from odoo import api, fields, models, _
from odoo.exceptions import UserError

# Blue Fox branded email wrapper. Colors substituted at render time from
# company.report_brand_primary / report_brand_dark (bf_lexend).
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
Matrice de connaissances</td>\
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
<strong style="color:{dark};">{company_name}</strong><br/>\
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


class MatrixSendWizard(models.TransientModel):
    _name = 'knowledge.matrix.send.wizard'
    _description = "Envoi du rapport de matrice de connaissances"

    matrix_id = fields.Many2one(
        'project.knowledge.matrix', required=True, string="Matrice",
    )
    recipient_ids = fields.Many2many(
        'res.partner', string="Destinataires",
    )
    subject = fields.Char(string="Sujet")
    body = fields.Html(string="Corps du message")
    preview_url = fields.Char(readonly=True)

    @api.onchange('matrix_id')
    def _onchange_matrix_id(self):
        if self.matrix_id:
            matrix = self.matrix_id
            self.recipient_ids = matrix.recipient_ids
            label = matrix.project_id.name if matrix.project_id else matrix.name
            self.subject = "Rapport de matrice \u2014 %s" % label
            progress = "%.0f" % matrix.progress
            self.body = (
                '<p style="font-size:16px;line-height:26px;color:#374151;'
                'margin:0 0 16px 0;">Bonjour,</p>'
                '<p style="font-size:16px;line-height:26px;color:#374151;'
                'margin:0 0 20px 0;">Veuillez trouver ci-joint le rapport '
                "de la matrice de connaissances pour "
                "<strong>%s</strong>.</p>"
                '<p style="font-size:16px;line-height:26px;color:#374151;'
                "margin:0 0 20px 0;\">"
                "Progression\u00a0: <strong>%s\u00a0%%</strong> "
                "(%d\u00a0/\u00a0%d &#233;l&#233;ments compl&#233;t&#233;s)"
                "</p>"
                '<p style="font-size:16px;line-height:26px;color:#374151;'
                'margin:0;">Cordialement,<br/>%s</p>'
            ) % (
                label,
                progress,
                matrix.completed_count,
                matrix.item_count,
                self.env.user.name,
            )

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for rec in records:
            if rec.matrix_id and not rec.recipient_ids:
                rec._onchange_matrix_id()
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
            company_phone=company.phone or '',
            company_website=website,
            logo_url='/web/image/res.company/%d/logo' % company.id,
            privacy_url=website.rstrip('/') + '/r/politique-de-confidentialite',
            terms_url=website.rstrip('/') + '/r/termes-et-conditions',
            content=str(inner_html or ''),
        ))

    def action_preview_pdf(self):
        """Generate PDF preview and re-open wizard with download link."""
        self.ensure_one()
        matrix = self.matrix_id
        matrix_name = (matrix.name or 'Matrice').replace(' ', '_')
        date_str = fields.Date.today().isoformat()

        pdf_data = matrix._get_pdf_binary()
        att = self.env['ir.attachment'].create({
            'name': "Apercu_Matrice_%s_%s.pdf" % (matrix_name, date_str),
            'type': 'binary',
            'datas': base64.b64encode(pdf_data),
            'mimetype': 'application/pdf',
            'res_model': matrix._name,
            'res_id': matrix.id,
        })
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
        """Generate PDF, attach it, and send branded email."""
        self.ensure_one()
        matrix = self.matrix_id

        if not self.recipient_ids:
            raise UserError(_("Veuillez s\u00e9lectionner au moins un destinataire."))

        # Generate PDF attachment
        matrix_name = (matrix.name or 'Matrice').replace(' ', '_')
        date_str = fields.Date.today().isoformat()

        pdf_data = matrix._get_pdf_binary()
        pdf_att = self.env['ir.attachment'].create({
            'name': "Matrice_%s_%s.pdf" % (matrix_name, date_str),
            'type': 'binary',
            'datas': base64.b64encode(pdf_data),
            'mimetype': 'application/pdf',
            'res_model': matrix._name,
            'res_id': matrix.id,
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
                'email_to': partner.email_formatted or partner.email,
                'recipient_ids': [(4, partner.id)],
                'attachment_ids': [(6, 0, [pdf_att.id])],
            }
            mail = self.env['mail.mail'].sudo().create(mail_values)
            mail.send()

        # Update last report date
        matrix.write({'last_report_date': fields.Datetime.now()})

        # Post a note on the chatter
        recipient_names = ', '.join(self.recipient_ids.mapped('name'))
        matrix.message_post(
            body=_("Rapport envoy\u00e9 \u00e0 %s") % recipient_names,
            message_type='comment',
            subtype_xmlid='mail.mt_note',
            attachment_ids=[pdf_att.id],
        )

        return {'type': 'ir.actions.act_window_close'}
