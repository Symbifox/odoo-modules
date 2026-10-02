import logging

from odoo import api, fields, models
from odoo.exceptions import UserError

from .document_section import body_hash

_logger = logging.getLogger(__name__)


class ProjectDocumentVersionBody(models.Model):
    """Gel du corps: une version publiée porte son propre contenu, figé."""
    _inherit = 'project.document.version'

    section_ids = fields.One2many(
        'project.document.version.section',
        'version_id',
        string='Sections gelées',
    )
    section_count = fields.Integer(
        string='Sections',
        compute='_compute_section_count',
    )
    sequence_index = fields.Integer(
        string='Rang',
        default=0,
        index=True,
        help="Ordre de publication, indépendant du numéro de version. "
             "Les numéros existants mélangent « 2.1 » et « 2025.11 »: seul ce "
             "rang permet de trier de façon fiable.",
    )
    body_hash = fields.Char(
        string='Empreinte du corps',
        readonly=True,
        copy=False,
    )
    frozen_date = fields.Datetime(
        string='Gelé le',
        readonly=True,
        copy=False,
    )
    frozen_uid = fields.Many2one(
        'res.users',
        string='Gelé par',
        readonly=True,
        copy=False,
    )
    changed_section_codes = fields.Char(
        string='Sections modifiées',
        readonly=True,
        copy=False,
        help='Sections dont le contenu diffère de la version précédente.',
    )
    body_source = fields.Selection(
        related='document_id.body_source',
        string='Source du corps',
    )
    is_erratum = fields.Boolean(
        string='Correctif mineur',
        compute='_compute_is_erratum',
        store=True,
        help="Un correctif ou une retouche de mise en forme n'invalide pas les "
             "accusés de réception déjà obtenus.",
    )

    @api.depends('section_ids')
    def _compute_section_count(self):
        for version in self:
            version.section_count = len(version.section_ids)

    @api.depends('change_type')
    def _compute_is_erratum(self):
        for version in self:
            version.is_erratum = version.change_type in ('patch', 'editorial')

    # ------------------------------------------------------------------
    # Gel
    # ------------------------------------------------------------------

    def _check_body_before_release(self, document):
        """Refuse un gel vide ou identique à la publication précédente."""
        self.ensure_one()
        missing = document.section_ids.filtered(
            lambda s: s.required and s.content_kind == 'html' and s.is_empty
        )
        if missing:
            raise UserError(
                "Sections obligatoires vides: %s.\n"
                "Remplissez-les avant de publier cette version."
                % ', '.join(missing.mapped('name'))
            )
        previous = self._previous_published(document)
        if previous and previous.body_hash == document.body_hash:
            raise UserError(
                "Le corps est identique à la version %s. Modifiez le contenu ou "
                "réutilisez la version existante." % previous.version_number
            )
        return previous

    def _previous_published(self, document):
        """La dernière version publiée ou remplacée du document, hors celle-ci."""
        self.ensure_one()
        return document.version_ids.filtered(
            lambda v: v.state in ('released', 'superseded') and v.id != self.id
        ).sorted(lambda v: (v.sequence_index or 0), reverse=True)[:1]

    def _freeze_body(self, previous):
        """Fige le corps vivant du document dans cette version.

        Crée l'instantané des sections (s'il n'existe pas déjà) et rend les
        valeurs d'empreinte à écrire sur la version. C'est le geste de la
        publication ; la migration 18.0.13.3.4 le rejoue tel quel pour les
        versions publiées avant que leur corps ne soit rédigé dans Odoo.
        """
        self.ensure_one()
        document = self.document_id
        changed = document._changed_section_codes(previous) if previous else \
            document.section_ids.mapped('code')
        vals = {
            'body_hash': document.body_hash,
            'changed_section_codes': ', '.join(changed) or False,
        }
        if not self.section_ids:
            # Les sections générées (gouvernance, approbations) sont rendues
            # au nom de CETTE version, pas de « la dernière publiée » telle que
            # le calcul stocké la voit à l'instant.
            Snapshot = self.env['project.document.version.section']
            sections = document.section_ids.with_context(
                pkm_render_version_id=self.id)
            for section in sections.sorted(lambda s: (s.sequence, s.id)):
                Snapshot.create(section._snapshot_vals(self))
        return vals

    @api.model
    def _freeze_unfrozen_released(self):
        """Fige le texte actuel des versions publiées qui n'ont pas de copie.

        Cas hérité : une version publiée quand le corps vivait sur Nextcloud,
        puis rédigé dans Odoo, n'a jamais eu d'instantané ; son PDF imprimait
        le corps vivant sous son numéro, avec la mention « non figé ». Décision
        retenue : figer le texte actuel comme copie de la version.

        Seules les versions à l'état « publiée », sans aucune section figée,
        d'un document dont le corps vit dans Odoo et porte des sections. Ni
        l'état, ni la date de publication, ni les distributions ne bougent, et
        rien ne part par courriel. Rejouée, la passe ne trouve plus rien.
        Rend les versions figées.
        """
        versions = self.with_context(
            active_test=False,
            tracking_disable=True,
            mail_notrack=True,
            mail_create_nolog=True,
        ).search([('state', '=', 'released')]).filtered(
            lambda v: not v.section_ids
            and v.document_id.body_source == 'internal'
            and v.document_id.section_ids
        )
        now = fields.Datetime.now()
        for version in versions.sorted('id'):
            document = version.document_id
            vals = version._freeze_body(version._previous_published(document))
            vals.update({'frozen_date': now, 'frozen_uid': self.env.user.id})
            version.write(vals)
            missing = document.section_ids.filtered(
                lambda s: s.required and s.content_kind == 'html' and s.is_empty
            )
            _logger.info(
                "project_knowledge_matrix : corps figé à partir du texte actuel "
                "pour %s v%s (document %s, version %s, %s sections)%s",
                document.code or document.name, version.version_number,
                document.id, version.id, len(version.section_ids),
                " ; sections obligatoires vides : %s" % ', '.join(missing.mapped('code'))
                if missing else '',
            )
        return versions

    def action_release(self):
        """Publie la version, et fige le corps s'il vit dans Odoo."""
        internal = self.filtered(lambda v: v.document_id.body_source == 'internal')
        previous_by_version = {}
        for version in internal:
            previous_by_version[version.id] = version._check_body_before_release(
                version.document_id
            )

        result = super().action_release()

        for version in self:
            previous = previous_by_version.get(
                version.id, self.env['project.document.version']
            )
            vals = {
                'frozen_date': fields.Datetime.now(),
                'frozen_uid': self.env.user.id,
            }
            if not version.sequence_index:
                vals['sequence_index'] = version._next_sequence_index()
            if previous and not version.previous_version_id:
                vals['previous_version_id'] = previous.id
            if version.id in previous_by_version:
                vals.update(version._freeze_body(previous))
            version.write(vals)
        return result

    def _next_sequence_index(self):
        self.ensure_one()
        siblings = self.search([
            ('document_id', '=', self.document_id.id),
        ])
        return max(siblings.mapped('sequence_index') or [0]) + 1

    def _recompute_body_hash_from_snapshot(self):
        """Recalcule l'empreinte à partir de l'instantané (rattrapage)."""
        for version in self:
            sections = version.section_ids.sorted(lambda s: (s.sequence, s.id))
            version.body_hash = body_hash([
                (s.code, s.content) for s in sections if s.content_kind == 'html'
            ])

    def action_generate_body_pdf(self):
        """PDF de CETTE version : son contenu, sous son numéro.

        Version publiée ou remplacée : son instantané figé. Version en
        préparation : le corps vivant, marqué « Brouillon, non approuvé ».
        """
        self.ensure_one()
        document = self.document_id
        if document.body_source != 'internal':
            raise UserError(
                "Ce document pointe vers un fichier externe: son corps ne vit "
                "pas dans Odoo, il n'y a rien à générer ici."
            )
        if not document._report_sections(version=self):
            if not self.section_ids and self.state not in ('draft', 'review', 'approved'):
                raise UserError(
                    "La version %s n'a pas de contenu figé dans Odoo : elle a été "
                    "publiée avant que le corps n'y soit rédigé. Son texte vit "
                    "dans le fichier joint à la version ou sur Nextcloud."
                    % self.version_number
                )
            raise UserError(
                "Aucune section remplie: rédigez le corps avant de générer le PDF."
            )
        return self.env.ref(
            'project_knowledge_matrix.action_report_document_version_body'
        ).report_action(self)

    def action_view_sections(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': f'Contenu gelé - {self.name}',
            'res_model': 'project.document.version.section',
            'view_mode': 'list,form',
            'domain': [('version_id', '=', self.id)],
        }
