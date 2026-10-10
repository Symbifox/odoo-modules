import base64

from markupsafe import Markup, escape

from odoo import api, fields, models, _
from odoo.exceptions import UserError
from odoo.tools import is_html_empty

# Mise en page commune des courriels maison : bf_onboarding_base la livre,
# bluefox_branding la remplace par la sienne quand il est installé. Elle porte
# le logo, les couleurs, le nom, l'adresse et le pied de la société.
MISE_EN_PAGE = 'bf_onboarding_base.bf_mail_layout'
_SURTITRE = Markup(
    '<p style="margin:0 0 4px 0; font-size:12px; font-weight:600; letter-spacing:0.6px; '
    'text-transform:uppercase; color:#6B7280;">%s</p>')


def render_branded_body(company, inner_html, record=None, titre="Matrice de connaissances"):
    """Habille ``inner_html`` (déjà sûr) de la mise en page commune des courriels.

    Ce corps part par ``mail.mail`` sans gabarit : on lui donne le contexte que
    ``mail.template.send_mail`` donne à une mise en page. Les valeurs de la
    société y entrent par QWeb, donc échappées. Sans la mise en page (socle trop
    ancien), le contenu part sans habillage plutôt que de ne pas partir.

    Elle remplace une coquille recopiée ici, qui portait le slogan et les liens
    de l'éditeur dans les courriels de tous les locataires.
    """
    env = company.env
    corps = _SURTITRE % titre + Markup(inner_html or '')
    contexte = {
        'message': env['mail.message'].sudo().new({
            'body': corps, 'record_name': record.display_name if record else False}),
        'subtype': env['mail.message.subtype'].sudo(),
        'model_description': env['ir.model']._get(record._name).display_name if record else False,
        'record': record,
        'record_name': False,
        'subtitles': False,
        'company': company,
        'email_add_signature': False,
        'signature': '',
        'website_url': '',
        'is_html_empty': is_html_empty,
    }
    html = env['ir.qweb']._render(
        MISE_EN_PAGE, contexte, minimal_qcontext=True, raise_if_not_found=False)
    if not html:
        return corps
    return Markup(env['mail.render.mixin']._replace_local_links(html))


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
                escape(label),
                progress,
                matrix.completed_count,
                matrix.item_count,
                escape(self.env.user.name),
            )

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for rec in records:
            if rec.matrix_id and not rec.recipient_ids:
                rec._onchange_matrix_id()
        return records

    def _wrap_branded_body(self, inner_html):
        """Habille le message de la mise en page commune, aux couleurs de la société
        de la matrice (pas celle de la session, voir `_pkm_company`)."""
        return render_branded_body(
            self.matrix_id._pkm_company(), inner_html, record=self.matrix_id)

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
