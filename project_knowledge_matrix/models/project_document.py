import logging
from datetime import timedelta

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


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
    company_id = fields.Many2one(
        'res.company',
        string='Société',
        required=True,
        default=lambda self: self.env.company,
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

    # Les alertes d'expiration ont leurs propres drapeaux. Tant qu'elles
    # partageaient ceux des révisions, un document portant les deux dates ne
    # recevait jamais que le rappel de révision : la passe le marquait, et la
    # recherche d'expiration l'excluait aussitôt. Porter les deux dates est le
    # cas courant, pas l'exception.
    expiration_reminder_sent_90 = fields.Boolean(
        string="Alerte d'expiration 90 jours envoyée",
        default=False,
        copy=False,
    )
    expiration_reminder_sent_60 = fields.Boolean(
        string="Alerte d'expiration 60 jours envoyée",
        default=False,
        copy=False,
    )
    expiration_reminder_sent_30 = fields.Boolean(
        string="Alerte d'expiration 30 jours envoyée",
        default=False,
        copy=False,
    )
    expiration_reminder_sent_7 = fields.Boolean(
        string="Alerte d'expiration 7 jours envoyée",
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

    @api.depends('version_ids.distribution_ids.state',
                 'version_ids.distribution_ids.is_outdated')
    def _compute_distribution_count(self):
        """Compteurs de distribution agrégés en une requête pour tout le lot.

        L'ancienne version faisait un ``search()`` par document. Comme
        ``distribution_count`` figure dans la liste et le kanban par défaut,
        afficher 205 documents coûtait 205 requêtes.
        """
        self.distribution_count = 0
        self.acknowledgment_count = 0
        self.pending_acknowledgment_count = 0
        self.outdated_distribution_count = 0

        doc_par_version = {
            version.id: doc.id
            for doc in self if doc.id
            for version in doc.version_ids if version.id
        }
        if not doc_par_version:
            return

        totaux = {}
        for version, etat, obsolete, nombre in self.env[
            'project.document.distribution'
        ]._read_group(
            [('version_id', 'in', list(doc_par_version))],
            groupby=['version_id', 'state', 'is_outdated'],
            aggregates=['__count'],
        ):
            entree = totaux.setdefault(
                doc_par_version[version.id],
                {'total': 0, 'accuses': 0, 'attente': 0, 'obsoletes': 0},
            )
            entree['total'] += nombre
            if etat == 'acknowledged':
                entree['accuses'] += nombre
            elif etat == 'pending':
                entree['attente'] += nombre
            if obsolete and etat in ('pending', 'acknowledged'):
                entree['obsoletes'] += nombre

        for doc in self:
            entree = totaux.get(doc.id)
            if entree:
                doc.distribution_count = entree['total']
                doc.acknowledgment_count = entree['accuses']
                doc.pending_acknowledgment_count = entree['attente']
                doc.outdated_distribution_count = entree['obsoletes']

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
            # Les drapeaux d'expiration ne bougent pas : réviser un document ne
            # repousse pas sa date d'expiration.

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

    # Paliers de rappel : (jours avant, drapeau de révision, drapeau
    # d'expiration, libellé). Chaque échéance a son propre drapeau — les deux
    # colonnes ne peuvent pas se marcher dessus.
    _REMINDER_INTERVALS = (
        (90, 'review_reminder_sent_90', 'expiration_reminder_sent_90', '90 jours'),
        (60, 'review_reminder_sent_60', 'expiration_reminder_sent_60', '60 jours'),
        (30, 'review_reminder_sent_30', 'expiration_reminder_sent_30', '30 jours'),
        (7, 'review_reminder_sent_7', 'expiration_reminder_sent_7', '7 jours'),
    )

    @api.model
    def _cron_document_maintenance(self):
        """Entretien quotidien des documents, en un seul réveil.

        Trois passes indépendantes qui avaient chacune leur tâche planifiée :
        les accusés en attente, les rappels de révision et d'expiration, et la
        documentation obsolète chez les clients. Deux d'entre elles partaient à
        la même minute.

        Chaque passe tourne dans son propre point de reprise. Sans ça, la fusion
        rendrait le module plus fragile qu'avant : trois crons, c'est trois
        transactions, et l'échec de l'une laissait les deux autres faire leur
        travail. Une passe qui lève est journalisée et les suivantes continuent.
        """
        passes = (
            ('accusés en attente',
             self.env['project.document.distribution']._cron_check_pending_acknowledgments),
            ('révisions et expirations', self._cron_check_document_reviews),
            ('documentation obsolète', self._cron_check_outdated_client_docs),
        )
        for libelle, passe in passes:
            try:
                with self.env.cr.savepoint():
                    passe()
            except Exception:
                # Le point de reprise a déjà défait les écritures de la passe ;
                # le cache de l'ORM, lui, garde ce qu'elle y avait mis.
                self.env.invalidate_all()
                _logger.exception(
                    "project_knowledge_matrix : la passe « %s » de l'entretien "
                    "quotidien a échoué. Les passes suivantes continuent.",
                    libelle,
                )

    @api.model
    def _cron_check_document_reviews(self):
        """Rappels de révision et alertes d'expiration, à quatre paliers.

        90, 60, 30 et 7 jours avant l'échéance, une activité par document et par
        palier. Un drapeau par palier empêche la répétition ; ``action_mark_reviewed``
        les remet à faux pour le cycle suivant.

        Les deux échéances sont indépendantes. Elles ont partagé un drapeau
        unique, et ça se voyait à l'usage : la passe traitait les révisions
        d'abord et marquait le document, puis la recherche d'expiration
        l'excluait sur ce même drapeau. Un document portant les deux dates ne
        recevait donc jamais son alerte d'expiration — soit la majorité du parc.
        """
        today = fields.Date.today()

        if not self.env.ref('mail.mail_activity_data_todo', raise_if_not_found=False):
            return

        # (champ de date, drapeau, titre, gabarit de phrase)
        echeances = (
            ('review_date', 1, 'Rappel de révision',
             'doit être révisé avant le'),
            ('expiration_date', 2, "Alerte d'expiration",
             'expire le'),
        )

        for jours, drapeau_revision, drapeau_expiration, libelle in self._REMINDER_INTERVALS:
            date_cible = today + timedelta(days=jours)
            drapeaux = {1: drapeau_revision, 2: drapeau_expiration}

            for champ_date, rang, titre, phrase in echeances:
                drapeau = drapeaux[rang]
                documents = self.search([
                    ('state', '=', 'active'),
                    (champ_date, '<=', date_cible),
                    (champ_date, '>', today),
                    (drapeau, '=', False),
                ])
                if not documents:
                    continue

                for doc in documents:
                    user = doc.owner_id or doc.author_id or self.env.user
                    echeance = doc[champ_date]
                    doc.activity_schedule(
                        'mail.mail_activity_data_todo',
                        date_deadline=echeance,
                        user_id=user.id,
                        note=f"<p><strong>{titre} ({libelle})</strong></p>"
                             f"<p>Le document <em>{doc.name}</em> ({doc.code}) "
                             f"{phrase} {echeance}.</p>",
                    )
                # Un seul write pour tout le palier plutôt qu'un par document.
                documents.write({drapeau: True})

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
