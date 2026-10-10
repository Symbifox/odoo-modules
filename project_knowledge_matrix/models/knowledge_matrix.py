import base64
import logging
from collections import OrderedDict

from markupsafe import escape

from odoo import api, fields, models, _

_logger = logging.getLogger(__name__)


class KnowledgeMatrix(models.Model):
    """Container for knowledge items linked to a project.

    A matrix groups related decision items for a specific project
    implementation. Can also serve as a reusable template.
    """
    _name = 'project.knowledge.matrix'
    _description = 'Project Knowledge Matrix'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'create_date desc'

    name = fields.Char(
        string='Name',
        required=True,
        tracking=True,
        help='Name of this knowledge matrix'
    )
    project_id = fields.Many2one(
        'project.project',
        string='Project',
        ondelete='cascade',
        tracking=True,
        index=True,
        help='Project this matrix belongs to'
    )
    company_id = fields.Many2one(
        'res.company',
        string='Company',
        default=lambda self: self.env.company,
        index=True
    )
    is_template = fields.Boolean(
        string='Is Template',
        default=False,
        help='Templates can be copied to new projects'
    )
    active = fields.Boolean(
        default=True,
        help='Inactive matrices are archived'
    )
    description = fields.Text(
        string='Description',
        help='Additional notes about this matrix'
    )

    # Relations
    item_ids = fields.One2many(
        'project.knowledge.item',
        'matrix_id',
        string='Items',
        copy=True
    )

    # Report sending
    recipient_ids = fields.Many2many(
        'res.partner',
        'knowledge_matrix_recipient_rel',
        'matrix_id',
        'partner_id',
        string='Destinataires du rapport',
        help='Contacts qui recevront le rapport PDF',
    )
    auto_send = fields.Boolean(
        string='Envoi automatique',
        default=False,
        tracking=True,
        help='Envoyer automatiquement le rapport selon la fr\u00e9quence choisie',
    )
    send_frequency = fields.Selection(
        selection=[
            ('weekly', 'Hebdomadaire'),
            ('biweekly', 'Aux deux semaines'),
            ('monthly', 'Mensuel'),
            ('custom_days', 'Intervalle personnalis\u00e9'),
        ],
        string='Fr\u00e9quence',
        default='monthly',
    )
    send_day_of_week = fields.Selection(
        selection=[
            ('0', 'Lundi'),
            ('1', 'Mardi'),
            ('2', 'Mercredi'),
            ('3', 'Jeudi'),
            ('4', 'Vendredi'),
            ('5', 'Samedi'),
            ('6', 'Dimanche'),
        ],
        string='Jour de la semaine',
        default='0',
    )
    send_day_of_month = fields.Integer(
        string='Jour du mois',
        default=1,
        help='Jour du mois (1-28) pour l\u2019envoi mensuel',
    )
    send_interval_days = fields.Integer(
        string='Intervalle (jours)',
        default=14,
        help='Nombre de jours entre chaque envoi',
    )
    last_report_date = fields.Datetime(
        string='Dernier envoi',
        readonly=True,
        tracking=True,
    )

    # Computed statistics
    item_count = fields.Integer(
        string='Total Items',
        compute='_compute_statistics',
        store=True
    )
    completed_count = fields.Integer(
        string='Completed Items',
        compute='_compute_statistics',
        store=True
    )
    pending_count = fields.Integer(
        string='Pending Items',
        compute='_compute_statistics',
        store=True
    )
    progress = fields.Float(
        string='Progress (%)',
        compute='_compute_statistics',
        store=True,
        group_operator='avg'
    )

    @api.depends('item_ids.state')
    def _compute_statistics(self):
        for matrix in self:
            items = matrix.item_ids.filtered(lambda i: i.state != 'na')
            total = len(items)
            done = len(items.filtered(lambda i: i.state in ('done', 'accepted')))
            pending = len(items.filtered(lambda i: i.state == 'pending'))

            matrix.item_count = total
            matrix.completed_count = done
            matrix.pending_count = pending
            matrix.progress = (done / total * 100) if total else 0.0

    def action_view_items(self):
        """Open list of items for this matrix."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': f'Items - {self.name}',
            'res_model': 'project.knowledge.item',
            'view_mode': 'list,form,kanban',
            'domain': [('matrix_id', '=', self.id)],
            'context': {
                'default_matrix_id': self.id,
                'search_default_group_section': 1,
            },
        }

    def action_duplicate_to_project(self):
        """Open wizard to copy this matrix to another project."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Copy Matrix to Project',
            'res_model': 'project.knowledge.matrix',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_name': f"{self.name} (Copy)",
                'default_is_template': False,
            },
        }

    def action_print_report(self):
        """Return the PDF report action for this matrix."""
        self.ensure_one()
        return self.env.ref(
            'project_knowledge_matrix.action_report_knowledge_matrix'
        ).report_action(self)

    def _get_report_data(self):
        """Prepare data for the PDF report template."""
        self.ensure_one()
        today = fields.Date.today()
        items = self.item_ids.filtered(lambda i: i.state != 'na')
        total = len(items)
        done = len(items.filtered(lambda i: i.state in ('done', 'accepted')))
        in_progress = len(items.filtered(lambda i: i.state == 'in_progress'))
        overdue = len(items.filtered(lambda i: i.is_overdue))
        progress = (done / total * 100) if total else 0.0

        # Group by section, sorted by section sequence
        sections_dict = OrderedDict()
        for item in items.sorted(key=lambda i: (
            i.section_id.sequence,
            i.section_id.id,
            i.phase or '',
            -(int(i.priority) if i.priority else 0),
        )):
            sec = item.section_id
            if sec.id not in sections_dict:
                sections_dict[sec.id] = {'section': sec, 'items': self.env['project.knowledge.item']}
            sections_dict[sec.id]['items'] |= item

        return {
            'today': str(today),
            'total': total,
            'done': done,
            'in_progress': in_progress,
            'overdue': overdue,
            'progress': progress,
            'sections': list(sections_dict.values()),
        }

    @api.model_create_multi
    def create(self, vals_list):
        """Set project from context if not provided.

        Sans société explicite, la matrice prend celle de son projet plutôt que
        celle de la session : c'est elle qui habille ses courriels et son PDF.
        """
        for vals in vals_list:
            if not vals.get('project_id') and self.env.context.get('default_project_id'):
                vals['project_id'] = self.env.context['default_project_id']
            if vals.get('project_id') and 'company_id' not in vals:
                societe = self.env['project.project'].browse(vals['project_id']).company_id
                if societe:
                    vals['company_id'] = societe.id
        return super().create(vals_list)

    def write(self, vals):
        """Un changement de projet aligne la société, sauf si elle est fournie.

        Seules les matrices qui CHANGENT de projet sont alignées : réécrire le même
        projet (import, modification en masse) garde une société posée exprès.
        """
        if vals.get('project_id') and 'company_id' not in vals:
            societe = self.env['project.project'].browse(vals['project_id']).company_id
            deplacees = self.filtered(lambda m: m.project_id.id != vals['project_id'])
            if societe and deplacees:
                super(KnowledgeMatrix, deplacees).write(dict(vals, company_id=societe.id))
                return super(KnowledgeMatrix, self - deplacees).write(vals)
        return super().write(vals)

    @api.onchange('project_id')
    def _onchange_project_id_company(self):
        if self.project_id.company_id:
            self.company_id = self.project_id.company_id

    def _pkm_company(self):
        """Société dont la matrice porte la marque : son champ Société, à défaut
        celle du projet, à défaut celle de la session.

        Courriel (bouton et envoi planifié) et PDF passent tous par ici. Lire
        `env.company` à la place peignait la matrice d'une société aux couleurs
        de la première société de la session.
        """
        self.ensure_one()
        return self.company_id or self.project_id.company_id or self.env.company

    # === PDF Report sending ===

    def _get_pdf_binary(self):
        """Render the branded PDF report and return raw bytes."""
        self.ensure_one()
        report = self.env.ref(
            'project_knowledge_matrix.action_report_knowledge_matrix'
        )
        pdf_content, _content_type = report._render_qweb_pdf(
            report.report_name, res_ids=[self.id]
        )
        return pdf_content

    def action_open_send_wizard(self):
        """Open the send-report wizard pre-filled for this matrix."""
        self.ensure_one()
        wizard = self.env['knowledge.matrix.send.wizard'].create({
            'matrix_id': self.id,
        })
        return {
            'type': 'ir.actions.act_window',
            'name': _("Envoyer le rapport"),
            'res_model': 'knowledge.matrix.send.wizard',
            'res_id': wizard.id,
            'views': [[False, 'form']],
            'target': 'new',
        }

    def _send_report_to_recipients(self):
        """Generate PDF, send branded email to recipients, update last_report_date.

        Used by both the wizard and the cron.
        """
        for matrix in self:
            recipients = matrix.recipient_ids.filtered('email')
            if not recipients:
                continue

            matrix_name = (matrix.name or 'Matrice').replace(' ', '_')
            date_str = fields.Date.today().isoformat()
            label = matrix.project_id.name if matrix.project_id else matrix.name

            # Generate PDF
            pdf_data = matrix._get_pdf_binary()
            pdf_att = self.env['ir.attachment'].create({
                'name': "Matrice_%s_%s.pdf" % (matrix_name, date_str),
                'type': 'binary',
                'datas': base64.b64encode(pdf_data),
                'mimetype': 'application/pdf',
                'res_model': matrix._name,
                'res_id': matrix.id,
            })

            company = matrix._pkm_company()
            # Build branded body
            progress = "%.0f" % matrix.progress
            inner_html = (
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
            ) % (escape(label), progress, matrix.completed_count, matrix.item_count,
                 escape(company.name or ''))

            # Même habillage que l'assistant : la mise en page commune.
            from odoo.addons.project_knowledge_matrix.wizard.matrix_send_wizard import (
                render_branded_body,
            )
            body_html = render_branded_body(company, inner_html, record=matrix)

            subject = "Rapport de matrice \u2014 %s" % label
            sender = self.env.user.email_formatted

            for partner in recipients:
                mail_values = {
                    'subject': subject,
                    'body_html': body_html,
                    'email_from': sender,
                    'email_to': partner.email_formatted or partner.email,
                    'recipient_ids': [(4, partner.id)],
                    'attachment_ids': [(6, 0, [pdf_att.id])],
                }
                mail = self.env['mail.mail'].sudo().create(mail_values)
                mail.send()

            matrix.write({'last_report_date': fields.Datetime.now()})

            recipient_names = ', '.join(recipients.mapped('name'))
            matrix.message_post(
                body=_("Rapport automatique envoy\u00e9 \u00e0 %s") % recipient_names,
                message_type='comment',
                subtype_xmlid='mail.mt_note',
                attachment_ids=[pdf_att.id],
            )

    @api.model
    def _cron_send_matrix_reports(self):
        """Daily cron: send reports for matrices with auto_send enabled and due."""
        today = fields.Date.today()
        matrices = self.search([
            ('auto_send', '=', True),
            ('is_template', '=', False),
            ('active', '=', True),
            ('recipient_ids', '!=', False),
        ])
        due = self.browse()
        for matrix in matrices:
            if matrix._is_report_due(today):
                due |= matrix

        if due:
            _logger.info(
                "Envoi automatique de rapports de matrice pour %d matrices.", len(due)
            )
            due._send_report_to_recipients()

    def _is_report_due(self, today):
        """Check whether this matrix's report is due today based on frequency."""
        self.ensure_one()
        freq = self.send_frequency

        if freq == 'weekly':
            return today.weekday() == int(self.send_day_of_week or '0')

        if freq == 'biweekly':
            if today.weekday() != int(self.send_day_of_week or '0'):
                return False
            # Even ISO weeks
            return today.isocalendar()[1] % 2 == 0

        if freq == 'monthly':
            target_day = min(self.send_day_of_month or 1, 28)
            return today.day == target_day

        if freq == 'custom_days':
            interval = self.send_interval_days or 14
            if not self.last_report_date:
                return True
            last = self.last_report_date.date()
            return (today - last).days >= interval

        return False
