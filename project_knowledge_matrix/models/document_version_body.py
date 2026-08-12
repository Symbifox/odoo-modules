from odoo import api, fields, models
from odoo.exceptions import UserError

from .document_section import body_hash


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
        previous = document.version_ids.filtered(
            lambda v: v.state in ('released', 'superseded') and v.id != self.id
        ).sorted(lambda v: (v.sequence_index or 0), reverse=True)
        if previous and previous[0].body_hash == document.body_hash:
            raise UserError(
                "Le corps est identique à la version %s. Modifiez le contenu ou "
                "réutilisez la version existante." % previous[0].version_number
            )
        return previous[0] if previous else self.env['project.document.version']

    def action_release(self):
        """Publie la version, et fige le corps s'il vit dans Odoo."""
        internal = self.filtered(lambda v: v.document_id.body_source == 'internal')
        previous_by_version = {}
        for version in internal:
            previous_by_version[version.id] = version._check_body_before_release(
                version.document_id
            )

        result = super().action_release()

        Snapshot = self.env['project.document.version.section']
        for version in self:
            document = version.document_id
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
                changed = document._changed_section_codes(previous) if previous else \
                    document.section_ids.mapped('code')
                vals['body_hash'] = document.body_hash
                vals['changed_section_codes'] = ', '.join(changed) or False
                if not version.section_ids:
                    for section in document.section_ids.sorted(
                            lambda s: (s.sequence, s.id)):
                        Snapshot.create(section._snapshot_vals(version))
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

    def action_view_sections(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': f'Contenu gelé - {self.name}',
            'res_model': 'project.document.version.section',
            'view_mode': 'list,form',
            'domain': [('version_id', '=', self.id)],
        }
