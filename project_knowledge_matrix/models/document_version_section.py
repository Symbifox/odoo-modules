from odoo import api, fields, models
from odoo.exceptions import UserError

from .document_section import section_hash


class ProjectDocumentVersionSection(models.Model):
    """Instantané figé d'une section, au moment du gel d'une version.

    Immuable par construction: c'est ce contenu-là qui a été publié, distribué
    et attesté. Le corps vivant continue d'évoluer de son côté.
    """
    _name = 'project.document.version.section'
    _description = 'Section de version de document'
    _order = 'sequence, id'

    version_id = fields.Many2one(
        'project.document.version',
        string='Version',
        required=True,
        ondelete='cascade',
        index=True,
    )
    code = fields.Char(string='Code', required=True, index=True)
    name = fields.Char(string='Titre', required=True)
    sequence = fields.Integer(string='Séquence', default=10)
    content = fields.Html(string='Contenu')
    content_hash = fields.Char(string='Empreinte', compute='_compute_content_hash', store=True)
    content_kind = fields.Selection(
        selection=[
            ('html', 'Rédigée'),
            ('computed', 'Générée par le système'),
        ],
        string='Nature du contenu',
        default='html',
    )
    page_break_before = fields.Boolean(string='Saut de page avant', default=False)
    document_id = fields.Many2one(
        related='version_id.document_id',
        string='Document',
        store=True,
        index=True,
    )
    company_id = fields.Many2one(
        related='version_id.document_id.company_id',
        string='Société',
        store=True,
        index=True,
    )

    _sql_constraints = [
        ('version_code_uniq', 'unique(version_id, code)',
         'Une section avec ce code existe déjà dans cette version!'),
    ]

    @api.depends('content')
    def _compute_content_hash(self):
        for rec in self:
            rec.content_hash = section_hash(rec.content)

    def write(self, vals):
        # Les champs calculés stockés doivent pouvoir s'écrire; le reste est figé.
        if set(vals) - {'content_hash'}:
            raise UserError(
                "Le contenu d'une version gelée ne peut plus être modifié. "
                "Créez une nouvelle version du document."
            )
        return super().write(vals)

    @api.ondelete(at_uninstall=False)
    def _unlink_never(self):
        raise UserError(
            "Une section de version publiée ne peut pas être supprimée. "
            "Retirez la version elle-même si elle a été gelée par erreur."
        )
