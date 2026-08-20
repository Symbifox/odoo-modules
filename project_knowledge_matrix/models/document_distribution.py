from odoo import api, fields, models
from odoo.exceptions import UserError


class ProjectDocumentDistribution(models.Model):
    """Track document distribution to clients and employees."""
    _name = 'project.document.distribution'
    _description = 'Distribution de document'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'distribution_date desc'

    # Relations
    version_id = fields.Many2one(
        'project.document.version',
        string='Version du document',
        required=True,
        ondelete='restrict',
        tracking=True,
    )

    # Recipient - either partner (client) or user (employee)
    recipient_type = fields.Selection([
        ('partner', 'Client / Partenaire'),
        ('employee', 'Employé'),
    ], string='Type de destinataire', default='partner', required=True, tracking=True)

    partner_id = fields.Many2one(
        'res.partner',
        string='Client',
        tracking=True,
        domain="[('is_company', '=', True)]",
    )
    user_id = fields.Many2one(
        'res.users',
        string='Employé',
        tracking=True,
        help='Employé devant accuser réception du document',
    )
    project_id = fields.Many2one(
        'project.project',
        string='Projet',
        tracking=True,
        help='Contexte de projet optionnel pour cette distribution',
    )

    # Distribution details
    distributed_by_id = fields.Many2one(
        'res.users',
        string='Distribué par',
        default=lambda self: self.env.user,
        tracking=True,
    )
    distribution_date = fields.Datetime(
        string='Date de distribution',
        default=fields.Datetime.now,
        required=True,
        tracking=True,
    )
    distribution_method = fields.Selection([
        ('email', 'Courriel'),
        ('portal', 'Portail client'),
        ('intranet', 'Intranet'),
        ('meeting', 'Réunion'),
        ('physical', 'Copie physique'),
        ('other', 'Autre'),
    ], string='Méthode de distribution', default='email', tracking=True)

    # Acknowledgment
    acknowledged = fields.Boolean(
        string='Accusé reçu',
        compute='_compute_acknowledged',
        store=True,
    )
    acknowledged_date = fields.Datetime(
        string='Date de l\'accusé',
        tracking=True,
    )
    acknowledged_by_partner_id = fields.Many2one(
        'res.partner',
        string='Accusé par (contact)',
        tracking=True,
        help='Contact client ayant accusé réception du document',
    )
    acknowledged_by_user_id = fields.Many2one(
        'res.users',
        string='Accusé par (employé)',
        tracking=True,
        help='Employé ayant accusé réception du document',
    )

    # Compliance tracking for internal documents
    requires_signature = fields.Boolean(
        string='Signature requise',
        default=False,
        help='Indique si une signature électronique ou manuscrite est requise',
    )
    signature_date = fields.Datetime(
        string='Date de signature',
        tracking=True,
    )
    compliance_notes = fields.Text(
        string='Notes de conformité',
        help='Notes relatives à la conformité et à la reconnaissance du document',
    )

    # Status
    state = fields.Selection([
        ('pending', 'En attente d\'accusé'),
        ('acknowledged', 'Accusé reçu'),
        ('superseded', 'Remplacé'),
        ('recalled', 'Rappelé'),
    ], string='État', default='pending', tracking=True, required=True)

    # Computed
    is_outdated = fields.Boolean(
        string='Obsolète',
        compute='_compute_is_outdated',
        store=True,
        help='Vrai si une version plus récente du document existe',
    )
    is_internal = fields.Boolean(
        related='version_id.is_internal',
        string='Document interne',
        store=True,
    )
    recipient_name = fields.Char(
        string='Destinataire',
        compute='_compute_recipient_name',
        store=True,
    )
    has_file_or_url = fields.Boolean(
        string='Fichier ou lien disponible',
        compute='_compute_has_file_or_url',
    )

    # Activity tracking for outdated notifications
    needs_update_activity_created = fields.Boolean(
        string='Activité de mise à jour créée',
        default=False,
        copy=False,
        help='Indique si une activité a été créée pour signaler la documentation obsolète',
    )

    # Notes
    notes = fields.Text(
        string='Notes',
    )

    # Related fields for convenience
    document_id = fields.Many2one(
        related='version_id.document_id',
        string='Document',
        store=True,
    )
    document_name = fields.Char(
        related='version_id.document_id.name',
        string='Nom du document',
    )
    version_number = fields.Char(
        related='version_id.version_number',
        string='Version',
    )
    document_type_id = fields.Many2one(
        related='version_id.document_id.type_id',
        string='Type de document',
        store=True,
    )
    current_version_id = fields.Many2one(
        related='version_id.document_id.latest_version_id',
        string='Version actuelle',
    )

    @api.depends('recipient_type', 'partner_id', 'partner_id.name',
                 'user_id', 'user_id.name')
    def _compute_recipient_name(self):
        """Nom du destinataire, tenu à jour avec la fiche liée.

        Le champ est stocké. Sans ``partner_id.name`` et ``user_id.name`` dans
        les dépendances, il fige le nom du jour de la distribution et ne le
        rejoue jamais : une fiche renommée depuis garde son ancien nom à
        l'écran, dans les rapports et dans les rappels d'accusé.
        """
        for dist in self:
            if dist.recipient_type == 'employee' and dist.user_id:
                dist.recipient_name = dist.user_id.name
            elif dist.partner_id:
                dist.recipient_name = dist.partner_id.name
            else:
                dist.recipient_name = False

    @api.depends('state')
    def _compute_acknowledged(self):
        for dist in self:
            dist.acknowledged = dist.state == 'acknowledged'

    @api.depends('version_id.attachment_id', 'version_id.attachment_ids', 'version_id.document_id.external_url')
    def _compute_has_file_or_url(self):
        for dist in self:
            has_file = bool(dist.version_id.attachment_id or dist.version_id.attachment_ids)
            has_url = bool(dist.version_id.document_id.external_url)
            dist.has_file_or_url = has_file or has_url

    @api.depends('version_id', 'version_id.document_id.latest_version_id')
    def _compute_is_outdated(self):
        for dist in self:
            if dist.version_id and dist.version_id.document_id.latest_version_id:
                dist.is_outdated = (
                    dist.version_id.id != dist.version_id.document_id.latest_version_id.id
                )
            else:
                dist.is_outdated = False

    def action_acknowledge(self):
        """Mark distribution as acknowledged."""
        vals = {
            'state': 'acknowledged',
            'acknowledged_date': fields.Datetime.now(),
        }
        for dist in self:
            if dist.recipient_type == 'employee':
                vals['acknowledged_by_user_id'] = dist.user_id.id
            else:
                vals['acknowledged_by_partner_id'] = dist.partner_id.id
        self.write(vals)

    def action_recall(self):
        """Recall this distribution."""
        self.write({'state': 'recalled'})

    def action_set_pending(self):
        """Reset to pending acknowledgment."""
        self.write({
            'state': 'pending',
            'acknowledged_date': False,
            'acknowledged_by_partner_id': False,
            'acknowledged_by_user_id': False,
            'signature_date': False,
        })

    def action_open_document(self):
        """Open the document form."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self.document_name,
            'res_model': 'project.document',
            'res_id': self.document_id.id,
            'view_mode': 'form',
        }

    def action_send_reminder(self):
        """Send acknowledgment reminder to client."""
        self.ensure_one()
        template = self.env.ref(
            'project_knowledge_matrix.mail_template_document_reminder',
            raise_if_not_found=False
        )
        if template:
            template.send_mail(self.id, force_send=True)
        return True

    def _collect_version_attachments(self, version):
        """Collect attachment IDs from a document version and ensure access tokens."""
        attachment_ids = []
        if version.attachment_id:
            attachment_ids.append(version.attachment_id.id)
        if version.attachment_ids:
            attachment_ids += version.attachment_ids.ids
        # Generate access tokens so download links in email body work
        if attachment_ids:
            attachments = self.env['ir.attachment'].sudo().browse(attachment_ids)
            attachments.generate_access_token()
        return attachment_ids

    def _send_template_with_attachments(self, template, attachment_ids):
        """Create mail from template, attach files, then send.

        Passing attachment_ids via email_values to send_mail() is unreliable
        in Odoo 18 — the attachments can be dropped during mail generation.
        Instead, create the mail first, link attachments via write(), then send.
        """
        mail_id = template.send_mail(self.id, force_send=False)
        if mail_id:
            mail = self.env['mail.mail'].sudo().browse(mail_id)
            if attachment_ids:
                mail.attachment_ids = [(4, aid) for aid in attachment_ids]
            mail.send()

    def action_send_document(self):
        """Open mail composer pre-filled from template so user can review before sending."""
        self.ensure_one()

        attachment_ids = self._collect_version_attachments(self.version_id)
        has_url = bool(self.document_id.external_url)

        if not attachment_ids and not has_url:
            raise UserError(
                "Impossible d'envoyer le courriel: aucun fichier n'est joint "
                "à la version et aucun lien externe n'est défini sur le document.\n\n"
                "Veuillez ajouter un fichier dans le champ « Fichier du document » "
                "de la version, ou un « Lien externe » sur la fiche du document."
            )

        template = self.env.ref(
            'project_knowledge_matrix.mail_template_document_distribution',
            raise_if_not_found=False
        )
        if not template:
            return True

        # Create composer pre-filled from template
        ctx = {
            'default_model': 'project.document.distribution',
            'default_res_ids': [self.id],
            'default_composition_mode': 'comment',
            'default_template_id': template.id,
        }
        composer = self.env['mail.compose.message'].with_context(**ctx).create({})

        # Add document file attachments on top of any template ones
        if attachment_ids:
            existing = composer.attachment_ids.ids
            all_ids = list(set(existing + attachment_ids))
            composer.attachment_ids = [(6, 0, all_ids)]

        return {
            'type': 'ir.actions.act_window',
            'name': 'Envoyer le document',
            'res_model': 'mail.compose.message',
            'view_mode': 'form',
            'res_id': composer.id,
            'target': 'new',
            'context': ctx,
        }

    def action_send_update_notification(self):
        """Open mail composer for update notification so user can review before sending."""
        self.ensure_one()
        template = self.env.ref(
            'project_knowledge_matrix.mail_template_document_update_available',
            raise_if_not_found=False
        )
        if not template:
            return True

        latest = self.version_id.document_id.latest_version_id
        attachment_ids = self._collect_version_attachments(latest) if latest else []

        ctx = {
            'default_model': 'project.document.distribution',
            'default_res_ids': [self.id],
            'default_composition_mode': 'comment',
            'default_template_id': template.id,
        }
        composer = self.env['mail.compose.message'].with_context(**ctx).create({})

        if attachment_ids:
            existing = composer.attachment_ids.ids
            all_ids = list(set(existing + attachment_ids))
            composer.attachment_ids = [(6, 0, all_ids)]

        return {
            'type': 'ir.actions.act_window',
            'name': 'Notifier mise à jour',
            'res_model': 'mail.compose.message',
            'view_mode': 'form',
            'res_id': composer.id,
            'target': 'new',
            'context': ctx,
        }

    @api.model
    def _cron_check_pending_acknowledgments(self):
        """Cron job to check for pending acknowledgments and send notifications."""
        # Find distributions pending acknowledgment for more than 7 days
        seven_days_ago = fields.Datetime.subtract(fields.Datetime.now(), days=7)
        pending_distributions = self.search([
            ('state', '=', 'pending'),
            ('distribution_date', '<', seven_days_ago),
        ])

        # Group by document owner and send digest
        owners = {}
        for dist in pending_distributions:
            owner = dist.document_id.owner_id or dist.document_id.author_id
            if owner:
                if owner.id not in owners:
                    owners[owner.id] = {
                        'user': owner,
                        'distributions': self.env['project.document.distribution'],
                    }
                owners[owner.id]['distributions'] |= dist

        # Create activities for follow-up
        for owner_data in owners.values():
            for dist in owner_data['distributions']:
                recipient = dist.recipient_name or 'Inconnu'
                dist.activity_schedule(
                    'mail.mail_activity_data_todo',
                    user_id=owner_data['user'].id,
                    note=f"Accusé de réception en attente pour {dist.version_id.name} de {recipient}",
                )
