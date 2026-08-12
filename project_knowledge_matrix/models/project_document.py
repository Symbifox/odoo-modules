from datetime import timedelta

from odoo import api, fields, models


class ProjectDocument(models.Model):
    """Master document template for client and internal documentation."""
    _name = 'project.document'
    _description = 'Document'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'name'

    # Identification
    name = fields.Char(
        string='Nom',
        required=True,
        tracking=True,
    )
    code = fields.Char(
        string='Référence',
        required=True,
        copy=False,
        tracking=True,
        help='Code de référence unique pour ce document',
    )

    # Minute book section
    minute_book_section = fields.Selection(
        selection=[
            ('charter', 'Statuts constitutifs'),
            ('bylaws', 'Reglements'),
            ('agreements', "Conventions d'actionnaires"),
            ('director_minutes', 'Proces-verbaux des administrateurs'),
            ('shareholder_minutes', 'Proces-verbaux des actionnaires'),
            ('forms_filed', 'Formulaires deposes'),
            ('financial_statements', 'Etats financiers'),
        ],
        string='Section du livre des minutes',
    )

    # Classification
    type_id = fields.Many2one(
        'project.document.type',
        string='Type',
        required=True,
        tracking=True,
    )
    is_internal = fields.Boolean(
        string='Document interne',
        related='type_id.is_internal',
        store=True,
        help='Document destiné à l\'usage interne de l\'entreprise',
    )

    # Language support
    language = fields.Selection([
        ('fr_CA', 'Français (Canada)'),
        ('en_CA', 'English (Canada)'),
        ('fr_FR', 'Français (France)'),
        ('en_US', 'English (US)'),
    ], string='Langue', default='fr_CA', required=True, tracking=True)

    translation_of_id = fields.Many2one(
        'project.document',
        string='Traduction de',
        tracking=True,
        help='Document source dont celui-ci est une traduction',
        domain="[('id', '!=', id)]",
    )
    translation_ids = fields.One2many(
        'project.document',
        'translation_of_id',
        string='Traductions',
        help='Versions traduites de ce document',
    )

    # Content matrix
    matrix_id = fields.Many2one(
        'project.knowledge.matrix',
        string='Matrice de contenu',
        tracking=True,
        help='Matrice de connaissances contenant le contenu structuré de ce document',
    )

    # External links
    external_url = fields.Char(
        string='Lien externe',
        help='URL vers le document sur Nextcloud ou autre système',
    )
    source_path = fields.Char(
        string='Chemin source',
        help='Chemin du fichier original (pour référence)',
    )
    project_id = fields.Many2one(
        'project.project',
        string='Projet',
        tracking=True,
        help='Laisser vide pour les documents à l\'échelle de l\'entreprise',
    )
    department_id = fields.Many2one(
        'hr.department',
        string='Département',
        tracking=True,
        help='Département responsable de ce document (pour les documents internes)',
    )
    company_id = fields.Many2one(
        'res.company',
        string='Société',
        required=True,
        default=lambda self: self.env.company,
    )

    # Software tracking
    software_id = fields.Many2one(
        'document.software',
        string='Logiciel',
        tracking=True,
        help='Logiciel documenté (ex: Nextcloud, Odoo)',
    )
    software_version_id = fields.Many2one(
        'document.software.version',
        string='Version du logiciel',
        tracking=True,
        domain="[('software_id', '=', software_id)]",
        help='Version spécifique du logiciel documentée',
    )

    # Expiration and review tracking
    review_date = fields.Date(
        string='Prochaine révision',
        tracking=True,
        help='Date à laquelle ce document devrait être révisé',
    )
    expiration_date = fields.Date(
        string='Date d\'expiration',
        tracking=True,
        help='Date après laquelle ce document n\'est plus valide',
    )
    review_interval_months = fields.Integer(
        string='Intervalle de révision (mois)',
        default=12,
        help='Fréquence de révision recommandée en mois',
    )
    last_review_date = fields.Date(
        string='Dernière révision',
        tracking=True,
    )

    # Activity tracking for reminders
    review_reminder_sent_90 = fields.Boolean(
        string='Rappel 90 jours envoyé',
        default=False,
        copy=False,
    )
    review_reminder_sent_60 = fields.Boolean(
        string='Rappel 60 jours envoyé',
        default=False,
        copy=False,
    )
    review_reminder_sent_30 = fields.Boolean(
        string='Rappel 30 jours envoyé',
        default=False,
        copy=False,
    )
    review_reminder_sent_7 = fields.Boolean(
        string='Rappel 7 jours envoyé',
        default=False,
        copy=False,
    )

    # Versioning
    current_version = fields.Char(
        string='Version actuelle',
        compute='_compute_current_version',
        store=True,
    )
    version_ids = fields.One2many(
        'project.document.version',
        'document_id',
        string='Versions',
    )
    latest_version_id = fields.Many2one(
        'project.document.version',
        string='Dernière version',
        compute='_compute_current_version',
        store=True,
    )

    # Metadata
    description = fields.Html(
        string='Description',
    )
    author_id = fields.Many2one(
        'res.users',
        string='Auteur',
        default=lambda self: self.env.user,
        tracking=True,
    )
    owner_id = fields.Many2one(
        'res.users',
        string='Responsable',
        tracking=True,
        help='Personne responsable de la maintenance de ce document',
    )

    # Status
    state = fields.Selection([
        ('draft', 'Brouillon'),
        ('active', 'Actif'),
        ('archived', 'Archivé'),
    ], string='État', default='draft', tracking=True, required=True)

    # Computed fields
    version_count = fields.Integer(
        string='Versions',
        compute='_compute_version_count',
    )
    distribution_count = fields.Integer(
        string='Distributions',
        compute='_compute_distribution_count',
    )
    acknowledgment_count = fields.Integer(
        string='Accusés de réception',
        compute='_compute_distribution_count',
    )
    pending_acknowledgment_count = fields.Integer(
        string='En attente',
        compute='_compute_distribution_count',
    )
    outdated_distribution_count = fields.Integer(
        string='Distributions obsolètes',
        compute='_compute_distribution_count',
    )

    # Expiration computed fields
    days_until_review = fields.Integer(
        string='Jours avant révision',
        compute='_compute_expiration_status',
    )
    days_until_expiration = fields.Integer(
        string='Jours avant expiration',
        compute='_compute_expiration_status',
    )
    is_review_due = fields.Boolean(
        string='Révision requise',
        compute='_compute_expiration_status',
        store=True,
    )
    is_expired = fields.Boolean(
        string='Expiré',
        compute='_compute_expiration_status',
        store=True,
    )
    is_expiring_soon = fields.Boolean(
        string='Expire bientôt',
        compute='_compute_expiration_status',
        store=True,
    )

    active = fields.Boolean(
        string='Actif',
        default=True,
    )

    _sql_constraints = [
        ('code_company_uniq', 'unique(code, company_id)',
         'Le code de référence doit être unique par société!'),
    ]

    @api.depends('version_ids', 'version_ids.state', 'version_ids.release_date')
    def _compute_current_version(self):
        for doc in self:
            released_versions = doc.version_ids.filtered(
                lambda v: v.state == 'released'
            ).sorted('release_date', reverse=True)
            if released_versions:
                doc.latest_version_id = released_versions[0]
                doc.current_version = released_versions[0].version_number
            else:
                doc.latest_version_id = False
                doc.current_version = False

    @api.depends('version_ids')
    def _compute_version_count(self):
        for doc in self:
            doc.version_count = len(doc.version_ids)

    def _compute_distribution_count(self):
        Distribution = self.env['project.document.distribution']
        for doc in self:
            distributions = Distribution.search([
                ('version_id.document_id', '=', doc.id)
            ])
            doc.distribution_count = len(distributions)
            doc.acknowledgment_count = len(distributions.filtered(
                lambda d: d.state == 'acknowledged'
            ))
            doc.pending_acknowledgment_count = len(distributions.filtered(
                lambda d: d.state == 'pending'
            ))
            doc.outdated_distribution_count = len(distributions.filtered(
                lambda d: d.is_outdated and d.state in ['pending', 'acknowledged']
            ))

    @api.depends('review_date', 'expiration_date')
    def _compute_expiration_status(self):
        today = fields.Date.today()
        for doc in self:
            # Review status
            if doc.review_date:
                delta = (doc.review_date - today).days
                doc.days_until_review = delta
                doc.is_review_due = delta <= 0
            else:
                doc.days_until_review = 999
                doc.is_review_due = False

            # Expiration status
            if doc.expiration_date:
                delta = (doc.expiration_date - today).days
                doc.days_until_expiration = delta
                doc.is_expired = delta < 0
                doc.is_expiring_soon = 0 <= delta <= 30
            else:
                doc.days_until_expiration = 999
                doc.is_expired = False
                doc.is_expiring_soon = False

    @api.onchange('software_id')
    def _onchange_software_id(self):
        """Clear software version when software changes."""
        if self.software_version_id and self.software_version_id.software_id != self.software_id:
            self.software_version_id = False

    def action_export_matrix_pdf(self):
        """Export the linked knowledge matrix as a branded PDF report."""
        self.ensure_one()
        if not self.matrix_id:
            from odoo.exceptions import UserError
            raise UserError("Aucune matrice de contenu liée à ce document.")
        return self.matrix_id.action_print_report()

    def action_view_matrix(self):
        """Open the linked knowledge matrix form."""
        self.ensure_one()
        if not self.matrix_id:
            from odoo.exceptions import UserError
            raise UserError("Aucune matrice de contenu liée à ce document.")
        return {
            'type': 'ir.actions.act_window',
            'name': self.matrix_id.name,
            'res_model': 'project.knowledge.matrix',
            'res_id': self.matrix_id.id,
            'view_mode': 'form',
        }

    def action_view_versions(self):
        """Open versions for this document."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': f'Versions - {self.name}',
            'res_model': 'project.document.version',
            'view_mode': 'list,form',
            'domain': [('document_id', '=', self.id)],
            'context': {
                'default_document_id': self.id,
            },
        }

    def action_view_distributions(self):
        """Open distributions for this document."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': f'Distributions - {self.name}',
            'res_model': 'project.document.distribution',
            'view_mode': 'list,kanban,form',
            'domain': [('version_id.document_id', '=', self.id)],
            'context': {
                'search_default_filter_active': 1,
            },
        }

    def action_set_active(self):
        """Set document to active state."""
        self.write({'state': 'active'})

    def action_set_archived(self):
        """Archive the document."""
        self.write({'state': 'archived'})

    def action_set_draft(self):
        """Set document back to draft."""
        self.write({'state': 'draft'})

    def action_mark_reviewed(self):
        """Mark document as reviewed and set next review date."""
        today = fields.Date.today()
        for doc in self:
            next_review = today + timedelta(days=doc.review_interval_months * 30)
            doc.write({
                'last_review_date': today,
                'review_date': next_review,
                'review_reminder_sent_90': False,
                'review_reminder_sent_60': False,
                'review_reminder_sent_30': False,
                'review_reminder_sent_7': False,
            })

    def action_view_outdated_distributions(self):
        """Open outdated distributions for this document."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': f'Distributions obsolètes - {self.name}',
            'res_model': 'project.document.distribution',
            'view_mode': 'list,kanban,form',
            'domain': [
                ('version_id.document_id', '=', self.id),
                ('is_outdated', '=', True),
                ('state', 'in', ['pending', 'acknowledged']),
            ],
        }

    @api.model
    def _cron_check_document_reviews(self):
        """Cron job to check for documents needing review and create activities.

        Creates activities at multiple intervals before review/expiration dates:
        - 90 days before: First reminder (low priority)
        - 60 days before: Second reminder (normal priority)
        - 30 days before: Third reminder (high priority)
        - 7 days before: Urgent reminder (urgent priority)
        """
        today = fields.Date.today()

        # Reminder intervals: (days_before, flag_field, priority, summary_prefix)
        intervals = [
            (90, 'review_reminder_sent_90', '0', '90 jours'),
            (60, 'review_reminder_sent_60', '1', '60 jours'),
            (30, 'review_reminder_sent_30', '2', '30 jours'),
            (7, 'review_reminder_sent_7', '3', '7 jours'),
        ]

        activity_type = self.env.ref('mail.mail_activity_data_todo', raise_if_not_found=False)
        if not activity_type:
            return

        for days, flag_field, priority, summary_prefix in intervals:
            target_date = today + timedelta(days=days)

            # Check review dates
            documents_review = self.search([
                ('state', '=', 'active'),
                ('review_date', '<=', target_date),
                ('review_date', '>', today),
                (flag_field, '=', False),
            ])

            for doc in documents_review:
                user = doc.owner_id or doc.author_id or self.env.user
                doc.activity_schedule(
                    'mail.mail_activity_data_todo',
                    date_deadline=doc.review_date,
                    user_id=user.id,
                    note=f"<p><strong>Rappel de révision ({summary_prefix})</strong></p>"
                         f"<p>Le document <em>{doc.name}</em> ({doc.code}) "
                         f"doit être révisé avant le {doc.review_date}.</p>",
                )
                doc.write({flag_field: True})

            # Check expiration dates
            documents_expiring = self.search([
                ('state', '=', 'active'),
                ('expiration_date', '<=', target_date),
                ('expiration_date', '>', today),
                (flag_field, '=', False),
            ])

            for doc in documents_expiring:
                user = doc.owner_id or doc.author_id or self.env.user
                doc.activity_schedule(
                    'mail.mail_activity_data_todo',
                    date_deadline=doc.expiration_date,
                    user_id=user.id,
                    note=f"<p><strong>Alerte d'expiration ({summary_prefix})</strong></p>"
                         f"<p>Le document <em>{doc.name}</em> ({doc.code}) "
                         f"expire le {doc.expiration_date}.</p>",
                )
                doc.write({flag_field: True})

    @api.model
    def _cron_check_outdated_client_docs(self):
        """Cron job to check for clients with outdated documentation.

        When a new version of a document is released, this checks which clients
        have older versions and creates activities for follow-up.
        """
        Distribution = self.env['project.document.distribution']
        today = fields.Date.today()

        # Find all outdated distributions that haven't been flagged yet
        outdated_distributions = Distribution.search([
            ('is_outdated', '=', True),
            ('state', 'in', ['pending', 'acknowledged']),
            ('needs_update_activity_created', '=', False),
        ])

        for dist in outdated_distributions:
            doc = dist.document_id
            user = doc.owner_id or doc.author_id or self.env.user
            recipient_name = dist.recipient_name or 'Client inconnu'

            dist.activity_schedule(
                'mail.mail_activity_data_todo',
                date_deadline=today + timedelta(days=7),
                user_id=user.id,
                note=f"<p><strong>Documentation obsolète à mettre à jour</strong></p>"
                     f"<p><em>{recipient_name}</em> dispose de la version {dist.version_number} "
                     f"du document <em>{doc.name}</em>.</p>"
                     f"<p>La version actuelle est <strong>{doc.current_version}</strong>.</p>"
                     f"<p>Veuillez distribuer la nouvelle version au client.</p>",
            )
            dist.write({'needs_update_activity_created': True})

    @api.model
    def _get_dashboard_report_data(self):
        """Compute dashboard data for the email report."""
        today = fields.Date.today()
        Document = self.env['project.document']
        Distribution = self.env['project.document.distribution']

        # Basic counts
        total_documents = Document.search_count([])
        active_documents = Document.search_count([('state', '=', 'active')])
        archived_documents = Document.search_count([('state', '=', 'archived')])
        internal_documents = Document.search_count([
            ('state', '=', 'active'),
            ('is_internal', '=', True),
        ])
        client_documents = Document.search_count([
            ('state', '=', 'active'),
            ('is_internal', '=', False),
        ])

        # Attention required
        overdue_review = Document.search_count([
            ('state', '=', 'active'),
            ('review_date', '<', today),
        ])
        expired_documents = Document.search_count([
            ('state', '=', 'active'),
            ('expiration_date', '<', today),
        ])
        expiring_30d = Document.search_count([
            ('state', '=', 'active'),
            ('expiration_date', '>=', today),
            ('expiration_date', '<=', today + timedelta(days=30)),
        ])

        # Review calendar
        review_0_30 = Document.search_count([
            ('state', '=', 'active'),
            ('review_date', '>=', today),
            ('review_date', '<=', today + timedelta(days=30)),
        ])
        review_30_60 = Document.search_count([
            ('state', '=', 'active'),
            ('review_date', '>', today + timedelta(days=30)),
            ('review_date', '<=', today + timedelta(days=60)),
        ])
        review_60_90 = Document.search_count([
            ('state', '=', 'active'),
            ('review_date', '>', today + timedelta(days=60)),
            ('review_date', '<=', today + timedelta(days=90)),
        ])

        # Client distribution metrics
        client_distributions = Distribution.search_count([
            ('recipient_type', '=', 'partner'),
        ])
        client_pending = Distribution.search_count([
            ('recipient_type', '=', 'partner'),
            ('state', '=', 'pending'),
        ])
        client_acknowledged = Distribution.search_count([
            ('recipient_type', '=', 'partner'),
            ('state', '=', 'acknowledged'),
        ])
        client_outdated = Distribution.search_count([
            ('recipient_type', '=', 'partner'),
            ('is_outdated', '=', True),
        ])
        client_ack_rate = round(
            (client_acknowledged / client_distributions * 100)
            if client_distributions > 0 else 0
        )

        # Internal compliance metrics
        internal_distributions = Distribution.search_count([
            ('recipient_type', '=', 'employee'),
        ])
        internal_pending = Distribution.search_count([
            ('recipient_type', '=', 'employee'),
            ('state', '=', 'pending'),
        ])
        internal_acknowledged = Distribution.search_count([
            ('recipient_type', '=', 'employee'),
            ('state', '=', 'acknowledged'),
        ])
        internal_compliance_rate = round(
            (internal_acknowledged / internal_distributions * 100)
            if internal_distributions > 0 else 0
        )

        # Credential metrics
        Credential = self.env['project.credential']
        credentials_total = Credential.search_count([('state', '=', 'active')])
        credentials_expiring = Credential.search_count([
            ('state', '=', 'active'),
            ('expiration_date', '>=', today),
            ('expiration_date', '<=', today + timedelta(days=30)),
        ])
        credentials_expired = Credential.search_count([
            ('state', '=', 'active'),
            ('expiration_date', '<', today),
        ])

        # Distribution activity
        first_of_month = today.replace(day=1)
        if first_of_month.month == 1:
            first_of_last_month = first_of_month.replace(year=first_of_month.year - 1, month=12)
        else:
            first_of_last_month = first_of_month.replace(month=first_of_month.month - 1)

        distributions_this_month = Distribution.search_count([
            ('distribution_date', '>=', first_of_month),
        ])
        distributions_last_month = Distribution.search_count([
            ('distribution_date', '>=', first_of_last_month),
            ('distribution_date', '<', first_of_month),
        ])

        # Overdue acknowledgments (7+ days)
        seven_days_ago = today - timedelta(days=7)
        overdue_acknowledgments = Distribution.search_count([
            ('state', '=', 'pending'),
            ('distribution_date', '<', seven_days_ago),
        ])

        # Content quality
        # NE PAS filtrer sur `version_count` : c'est un calculé NON STOCKÉ, et
        # Odoo 18 écarte le critère en silence au lieu de lever -> le compteur
        # rendait le nombre de documents actifs (191) au lieu des documents
        # sans version (13). Le one2many, lui, est cherchable.
        docs_without_versions = Document.search_count([
            ('state', '=', 'active'),
            ('version_ids', '=', False),
        ])

        # Decision tracking metrics
        # Only count decisions living in active (non-archived) matrices, so that
        # archiving a strategic matrix removes its decisions from the dashboard.
        Item = self.env['project.knowledge.item']
        decision_base = [
            ('item_type', '=', 'decision'),
            ('matrix_id.active', '=', True),
        ]
        decisions_total = Item.search_count(decision_base)
        decisions_accepted = Item.search_count(decision_base + [
            ('state', '=', 'accepted'),
        ])
        decisions_proposed = Item.search_count(decision_base + [
            ('state', '=', 'proposed'),
        ])
        decisions_rejected = Item.search_count(decision_base + [
            ('state', '=', 'rejected'),
        ])
        decisions_high_impact = Item.search_count(decision_base + [
            ('impact_level', '=', 'high'),
            ('state', 'in', ['pending', 'proposed']),
        ])

        # Corporate governance metrics
        Director = self.env['corporate.director']
        Officer = self.env['corporate.officer']
        Compliance = self.env['corporate.compliance.event']
        Resolution = self.env['corporate.resolution']

        corp_active_directors = Director.search_count([('is_active', '=', True)])
        corp_active_officers = Officer.search_count([('is_active', '=', True)])
        corp_overdue_compliance = Compliance.search_count([
            ('completed_date', '=', False),
            ('status', '=', 'overdue'),
        ])
        corp_due_soon_compliance = Compliance.search_count([
            ('completed_date', '=', False),
            ('status', '=', 'due_soon'),
        ])
        corp_adopted_resolutions = Resolution.search_count([
            ('status', '=', 'adopted'),
        ])

        # Documents by type
        documents_by_type = []
        doc_types = self.env['project.document.type'].search([])
        for doc_type in doc_types:
            count = Document.search_count([
                ('state', '=', 'active'),
                ('type_id', '=', doc_type.id),
            ])
            if count > 0:
                documents_by_type.append((doc_type.name, count))
        documents_by_type.sort(key=lambda x: x[1], reverse=True)

        # Dashboard URL
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        dashboard_url = f"{base_url}/web#action=project_knowledge_matrix.action_knowledge_dashboard"

        return {
            'links': self._get_report_links(base_url, dashboard_url),
            'report_date': today.strftime('%d/%m/%Y'),
            'total_documents': total_documents,
            'active_documents': active_documents,
            'archived_documents': archived_documents,
            'internal_documents': internal_documents,
            'client_documents': client_documents,
            'overdue_review': overdue_review,
            'expired_documents': expired_documents,
            'expiring_30d': expiring_30d,
            'review_0_30': review_0_30,
            'review_30_60': review_30_60,
            'review_60_90': review_60_90,
            # Client metrics
            'client_distributions': client_distributions,
            'client_pending': client_pending,
            'client_outdated': client_outdated,
            'client_ack_rate': client_ack_rate,
            # Internal metrics
            'internal_distributions': internal_distributions,
            'internal_pending': internal_pending,
            'internal_compliance_rate': internal_compliance_rate,
            # Credentials
            'credentials_total': credentials_total,
            'credentials_expiring': credentials_expiring,
            'credentials_expired': credentials_expired,
            # Activity
            'distributions_this_month': distributions_this_month,
            'distributions_last_month': distributions_last_month,
            'overdue_acknowledgments': overdue_acknowledgments,
            # Quality
            'docs_without_versions': docs_without_versions,
            # Decisions
            'decisions_total': decisions_total,
            'decisions_accepted': decisions_accepted,
            'decisions_proposed': decisions_proposed,
            'decisions_rejected': decisions_rejected,
            'decisions_high_impact': decisions_high_impact,
            # Corporate governance
            'corp_active_directors': corp_active_directors,
            'corp_active_officers': corp_active_officers,
            'corp_overdue_compliance': corp_overdue_compliance,
            'corp_due_soon_compliance': corp_due_soon_compliance,
            'corp_adopted_resolutions': corp_adopted_resolutions,
            # Other
            'documents_by_type': documents_by_type[:10],
            'dashboard_url': dashboard_url,
        }

    # Chaque clé est un compteur de `_get_dashboard_report_data`; la valeur est
    # l'action dont le domaine reproduit ce compteur. Ajouter un chiffre au
    # courriel = ajouter son entrée ici ET son action dans
    # views/report_drilldown_actions.xml, sinon le lien retombe muettement sur
    # le tableau de bord.
    _REPORT_LINK_ACTIONS = {
        # Aperçu
        'active_documents': 'report_action_docs_active',
        'internal_documents': 'report_action_docs_internal',
        'client_documents': 'report_action_docs_client',
        'archived_documents': 'report_action_docs_archived',
        'documents_by_type': 'report_action_docs_by_type',
        # Attention requise
        'expired_documents': 'report_action_docs_expired',
        'overdue_review': 'report_action_docs_overdue_review',
        'expiring_30d': 'report_action_docs_expiring_30d',
        # Calendrier des révisions
        'review_0_30': 'report_action_docs_review_0_30',
        'review_30_60': 'report_action_docs_review_30_60',
        'review_60_90': 'report_action_docs_review_60_90',
        # Qualité
        'docs_without_versions': 'report_action_docs_without_version',
        # Documentation clients
        'client_distributions': 'report_action_dist_client',
        'client_ack_rate': 'report_action_dist_client_ack',
        'client_pending': 'report_action_dist_client_pending',
        'client_outdated': 'report_action_dist_client_outdated',
        # Conformité interne
        'internal_distributions': 'report_action_dist_internal',
        'internal_compliance_rate': 'report_action_dist_internal_ack',
        'internal_pending': 'report_action_dist_internal_pending',
        'overdue_acknowledgments': 'report_action_dist_overdue_ack',
        # Activité
        'distributions_this_month': 'report_action_dist_this_month',
        'distributions_last_month': 'report_action_dist_last_month',
        # Identifiants
        'credentials_total': 'report_action_cred_active',
        'credentials_expiring': 'report_action_cred_expiring',
        'credentials_expired': 'report_action_cred_expired',
        # Décisions
        'decisions_total': 'report_action_decisions_all',
        'decisions_accepted': 'report_action_decisions_accepted',
        'decisions_proposed': 'report_action_decisions_proposed',
        'decisions_rejected': 'report_action_decisions_rejected',
        'decisions_high_impact': 'report_action_decisions_high_impact',
        # Gouvernance corporative
        'corp_active_directors': 'report_action_corp_directors',
        'corp_active_officers': 'report_action_corp_officers',
        'corp_adopted_resolutions': 'report_action_corp_resolutions_adopted',
        'corp_overdue_compliance': 'report_action_corp_compliance_overdue',
        'corp_due_soon_compliance': 'report_action_corp_compliance_due_soon',
    }

    @api.model
    def _get_report_links(self, base_url, fallback_url):
        """Construire un lien de forage par chiffre du rapport.

        On résout l'action en ID NUMÉRIQUE plutôt que de poser son xmlid dans
        l'URL : `/odoo/action-<id>` est la forme vérifiée du routeur Odoo 18.

        Le routeur étant côté client, une URL fautive ne rend pas de 404 — la
        page charge puis échoue en silence. D'où le repli sur le tableau de
        bord quand une action manque (module partiellement mis à jour) plutôt
        qu'un '#' qui ne mène nulle part.
        """
        links = {}
        for key, action_xmlid in self._REPORT_LINK_ACTIONS.items():
            action = self.env.ref(
                f'project_knowledge_matrix.{action_xmlid}',
                raise_if_not_found=False,
            )
            links[key] = (
                f"{base_url}/odoo/action-{action.id}" if action else fallback_url
            )
        return links

    @api.model
    def _cron_send_dashboard_report(self):
        """Cron job to send biweekly dashboard report email."""
        self._send_dashboard_report()

    @api.model
    def send_dashboard_report_now(self):
        """Public method to send dashboard report (callable via RPC)."""
        self._send_dashboard_report()
        return True

    @api.model
    def _send_dashboard_report(self, recipient_emails=None):
        """Send the dashboard report email to specified recipients.

        Args:
            recipient_emails: List of email addresses. If None, uses configured recipients.
        """
        # Get recipients from config or parameter
        if not recipient_emails:
            config_param = self.env['ir.config_parameter'].sudo()
            recipients_str = config_param.get_param(
                'project_knowledge_matrix.dashboard_report_recipients', ''
            )
            recipient_emails = [e.strip() for e in recipients_str.split(',') if e.strip()]

        if not recipient_emails:
            # Default to Knowledge Matrix managers
            manager_group = self.env.ref(
                'project_knowledge_matrix.group_document_manager',
                raise_if_not_found=False
            )
            if manager_group:
                recipient_emails = [
                    u.email for u in manager_group.users if u.email
                ]

        if not recipient_emails:
            return False

        # Get report data
        report_data = self._get_dashboard_report_data()

        # Get mail template
        template = self.env.ref(
            'project_knowledge_matrix.mail_template_document_dashboard_report',
            raise_if_not_found=False
        )
        if not template:
            return False

        # Send to each recipient
        company = self.env.company
        for email in recipient_emails:
            ctx = dict(report_data)
            ctx['recipient_email'] = email
            template.with_context(ctx).send_mail(
                company.id,
                force_send=True,
                email_values={'email_to': email},
            )

        return True
