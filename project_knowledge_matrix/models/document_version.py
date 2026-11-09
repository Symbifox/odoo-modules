import logging
from datetime import datetime, time

from odoo import api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)

# Une date d'entrée en vigueur n'a pas d'heure. À minuit UTC, elle s'affiche la
# VEILLE pour un lecteur québécois (UTC-5) ; à 05:00 UTC, elle tombe le bon jour
# à Montréal (minuit, ou 01:00 l'été) comme à Auckland (17:00 ou 18:00).
HEURE_DE_PUBLICATION = 5


def instant_de_publication(date):
    """L'instant que vaut une date de publication qui n'a pas d'heure.

    Une date-heure déjà complète est rendue telle quelle : la fonction comble
    une heure manquante, elle n'en impose pas une.
    """
    if not date:
        return None
    if isinstance(date, datetime):
        return date
    return datetime.combine(fields.Date.to_date(date), time(HEURE_DE_PUBLICATION))


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

    distribution_ids = fields.One2many(
        'project.document.distribution',
        'version_id',
        string='Distributions',
    )

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

    def _cle_de_publication(self):
        """Clé de tri d'une version publiée, qui n'exige pas de date.

        ``release_date`` est posée par le bouton de publication, jamais par la
        création : une version créée directement à l'état publié par l'ORM, en
        RPC ou par un script de reprise n'en a pas. Trier le champ brut
        comparait alors un booléen à une date, le calcul cassait au vidage de
        la transaction et la fiche du document devenait illisible. Le repli
        descend sur la date d'entrée en vigueur, puis sur l'identifiant.

        🔴 L'identifiant est celui de l'ORIGINE, pas ``self.id`` : dans un
        onchange, toute ligne du tableau des versions est un ``NewId``, et
        ``NewId`` n'a pas de ``__lt__``. Deux versions qui égalisent sur la date
        faisaient alors lever un ``TypeError`` dans le formulaire — un chemin
        que l'ancien tri brut ne touchait pas, puisqu'il ne comparait que des
        dates. Une ligne encore neuve vaut 0.

        ⚠️ Le rang de publication (``sequence_index``) n'entre PAS dans la clé,
        malgré son nom : il n'est posé que par le bouton, donc une version
        publiée par un script garde 0 — le rang le plus bas en étant la plus
        récente — et la migration qui l'a semé ne garantit ni son unicité ni
        son sens. Un document peut donc porter deux versions de même rang, ou
        une ligne de rang 0 plus récente que sa voisine mieux classée.
        """
        self.ensure_one()
        date = self.release_date or instant_de_publication(self.effective_date)
        return (date or datetime.min, self._origin.id or 0)

    @api.constrains('state', 'release_date')
    def _check_date_de_publication(self):
        """L'état publié exige une date de publication.

        Le bouton « Publier » la pose toujours ; l'ORM, le RPC et les scripts
        de reprise, non. Une version publiée sans date mélangée à des versions
        datées cassait le tri du document : le défaut se dit ici, à
        l'écriture, plutôt qu'au vidage de la transaction, où il refusait
        l'écriture entière sans nommer la version fautive.
        """
        for version in self:
            if version.state == 'released' and not version.release_date:
                raise ValidationError(
                    "La version %s de « %s » est à l'état publié sans date de "
                    "publication. Publiez-la par le bouton « Publier », qui "
                    "pose la date, ou donnez-lui une date de publication."
                    % (version.version_number or '?',
                       version.document_id.display_name or '?')
                )

    @api.model
    def _dater_les_publiees_sans_date(self):
        """Donne une date de publication aux versions qui ont été publiées sans.

        Rattrapage joué par la migration 18.0.13.6.0. Le bouton
        « Publier » pose la date ; une version créée directement à l'état
        publié par un script de reprise, non. Ces versions ne cassaient rien
        tant qu'aucune version datée du même document n'existait, et cassaient
        la fiche entière au premier mélange.

        La date prise est celle qui dit le mieux quand la version est devenue
        publique : sa date d'entrée en vigueur (à 05:00 UTC, voir
        ``instant_de_publication``), sinon son approbation, sinon le gel de son
        corps, sinon sa création, et à défaut de tout cela l'instant de la
        passe. 🔴 Ce dernier repli n'est pas décoratif : une ligne entrée en SQL
        brut peut n'avoir aucune date, même pas ``create_date``, et écrire
        ``False`` sur une version publiée ferait lever la contrainte que ce même
        correctif installe — la migration avorterait et le ``-u`` roulerait en
        arrière. L'état, le corps figé et les distributions ne bougent pas, et
        rien ne part par courriel. Rejouée, la passe ne trouve plus rien.
        """
        versions = self.with_context(
            tracking_disable=True,
            mail_notrack=True,
            mail_create_nolog=True,
        ).search([
            ('state', 'in', ('released', 'superseded')),
            ('release_date', '=', False),
        ])
        for version in versions:
            date = (
                instant_de_publication(version.effective_date)
                or version.approval_date
                or version.frozen_date
                or version.create_date
                or fields.Datetime.now()
            )
            version.release_date = date
            _logger.info(
                "project_knowledge_matrix : date de publication posée sur "
                "%s v%s (document %s, version %s, état %s) : %s",
                version.document_id.code or version.document_id.name,
                version.version_number, version.document_id.id, version.id,
                version.state, date,
            )
        return versions

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

    @api.depends('distribution_ids.state')
    def _compute_distribution_stats(self):
        """Compteurs agrégés en une requête pour tout le lot.

        L'ancienne version faisait un ``search()`` par version : afficher une
        liste de N versions coûtait N requêtes.
        """
        self.distribution_count = 0
        self.pending_acknowledgment_count = 0
        ids_reels = [v.id for v in self if v.id]
        if not ids_reels:
            return

        totaux = {}
        for version, etat, nombre in self.env['project.document.distribution']._read_group(
            [('version_id', 'in', ids_reels)],
            groupby=['version_id', 'state'],
            aggregates=['__count'],
        ):
            entree = totaux.setdefault(version.id, {'total': 0, 'attente': 0})
            entree['total'] += nombre
            if etat == 'pending':
                entree['attente'] += nombre

        for version in self:
            entree = totaux.get(version.id)
            if entree:
                version.distribution_count = entree['total']
                version.pending_acknowledgment_count = entree['attente']

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
