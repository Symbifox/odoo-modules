from odoo import api, fields, models


class ProjectDocumentVersion(models.Model):
    """Individual document versions with changelog and attachments."""
    _name = 'project.document.version'
    _description = 'Version de document'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'document_id, release_date desc, version_number desc'

    # Identification
    name = fields.Char(
        string='Nom',
        compute='_compute_name',
        store=True,
    )
    version_number = fields.Char(
        string='Version',
        required=True,
        tracking=True,
        help='Numéro de version (ex: 1.0, 1.1, 2.0)',
    )

    # Relations
    document_id = fields.Many2one(
        'project.document',
        string='Document',
        required=True,
        ondelete='cascade',
    )
    previous_version_id = fields.Many2one(
        'project.document.version',
        string='Version précédente',
        domain="[('document_id', '=', document_id), ('id', '!=', id)]",
    )

    # Content
    attachment_id = fields.Many2one(
        'ir.attachment',
        string='Fichier du document',
        help='Le fichier principal pour cette version',
    )
    attachment_ids = fields.Many2many(
        'ir.attachment',
        'project_document_version_attachment_rel',
        'version_id',
        'attachment_id',
        string='Fichiers supplémentaires',
        help='Fichiers complémentaires pour cette version',
    )

    # Version change tracking
    change_type = fields.Selection([
        ('major', 'Majeure - Refonte complète'),
        ('minor', 'Mineure - Ajouts/modifications'),
        ('patch', 'Correctif - Corrections mineures'),
        ('editorial', 'Éditoriale - Mise en forme'),
    ], string='Type de modification', default='minor', tracking=True,
       help='Nature des changements dans cette version')
    change_summary = fields.Char(
        string='Résumé des modifications',
        tracking=True,
        help='Résumé court des changements (ex: "Mise à jour politique vacances")',
    )
    changelog = fields.Text(
        string='Journal des modifications',
        help='Description détaillée des changements (format Markdown supporté)',
    )

    # Approval workflow
    approved_by_id = fields.Many2one(
        'res.users',
        string='Approuvé par',
        tracking=True,
        help='Personne ayant approuvé cette version',
    )
    approval_date = fields.Datetime(
        string='Date d\'approbation',
        tracking=True,
    )

    # Metadata
    author_id = fields.Many2one(
        'res.users',
        string='Auteur',
        default=lambda self: self.env.user,
        tracking=True,
    )
    effective_date = fields.Date(
        string='Date d\'entrée en vigueur',
        tracking=True,
        help='Date à laquelle cette version devient effective',
    )
    expiration_date = fields.Date(
        string='Date d\'expiration',
        tracking=True,
        help='Date d\'expiration de cette version (optionnel)',
    )
    release_date = fields.Datetime(
        string='Date de publication',
        tracking=True,
        help='Date et heure de publication de cette version',
    )
    review_date = fields.Date(
        string='Prochaine révision',
        tracking=True,
        help='Date de révision prévue pour ce document',
    )

    # Status
    state = fields.Selection([
        ('draft', 'Brouillon'),
        ('review', 'En révision'),
        ('approved', 'Approuvé'),
        ('released', 'Publié'),
        ('superseded', 'Remplacé'),
        ('withdrawn', 'Retiré'),
    ], string='État', default='draft', tracking=True, required=True)

    # Computed fields
    is_current = fields.Boolean(
        string='Version actuelle',
        compute='_compute_is_current',
        store=True,
    )
    distribution_count = fields.Integer(
        string='Distributions',
        compute='_compute_distribution_stats',
    )
    pending_acknowledgment_count = fields.Integer(
        string='Accusés en attente',
        compute='_compute_distribution_stats',
    )

    # Related fields for convenience
    document_type_id = fields.Many2one(
        related='document_id.type_id',
        string='Type de document',
        store=True,
    )
    project_id = fields.Many2one(
        related='document_id.project_id',
        string='Projet',
        store=True,
    )
    is_internal = fields.Boolean(
        related='document_id.is_internal',
        string='Document interne',
        store=True,
    )

    _sql_constraints = [
        ('version_document_uniq', 'unique(version_number, document_id)',
         'Le numéro de version doit être unique par document!'),
    ]

    @api.depends('document_id.name', 'version_number')
    def _compute_name(self):
        for version in self:
            if version.document_id and version.version_number:
                version.name = f"{version.document_id.name} v{version.version_number}"
            else:
                version.name = False

    @api.depends('document_id.latest_version_id')
    def _compute_is_current(self):
        for version in self:
            version.is_current = (
                version.document_id.latest_version_id.id == version.id
                if version.document_id.latest_version_id else False
            )

    def _compute_distribution_stats(self):
        Distribution = self.env['project.document.distribution']
        for version in self:
            distributions = Distribution.search([('version_id', '=', version.id)])
            version.distribution_count = len(distributions)
            version.pending_acknowledgment_count = len(
                distributions.filtered(lambda d: d.state == 'pending')
            )

    def action_submit_review(self):
        """Submit version for review."""
        self.write({'state': 'review'})

    def action_approve(self):
        """Approve this version."""
        self.write({
            'state': 'approved',
            'approved_by_id': self.env.user.id,
            'approval_date': fields.Datetime.now(),
        })

    def action_release(self):
        """Release this version, marking any previous released version as superseded."""
        for version in self:
            # Mark previous released versions as superseded
            previous_released = version.document_id.version_ids.filtered(
                lambda v: v.state == 'released' and v.id != version.id
            )
            previous_released.write({'state': 'superseded'})

            # Mark distributions of superseded versions as outdated
            Distribution = self.env['project.document.distribution']
            outdated_distributions = Distribution.search([
                ('version_id', 'in', previous_released.ids),
                ('state', 'in', ['pending', 'acknowledged']),
            ])
            outdated_distributions.write({'state': 'superseded'})

            # Release this version
            vals = {
                'state': 'released',
                'release_date': fields.Datetime.now(),
            }
            # Auto-approve if not already approved
            if not version.approved_by_id:
                vals['approved_by_id'] = self.env.user.id
                vals['approval_date'] = fields.Datetime.now()
            version.write(vals)

    def action_withdraw(self):
        """Withdraw this version."""
        self.write({'state': 'withdrawn'})

    def action_set_draft(self):
        """Set version back to draft."""
        self.write({
            'state': 'draft',
            'release_date': False,
            'approved_by_id': False,
            'approval_date': False,
        })

    def action_view_distributions(self):
        """Open distributions for this version."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': f'Distributions - {self.name}',
            'res_model': 'project.document.distribution',
            'view_mode': 'list,kanban,form',
            'domain': [('version_id', '=', self.id)],
            'context': {
                'default_version_id': self.id,
            },
        }

    def action_create_distribution(self):
        """Quick action to create a new distribution."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Distribute Document',
            'res_model': 'project.document.distribution',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_version_id': self.id,
            },
        }
