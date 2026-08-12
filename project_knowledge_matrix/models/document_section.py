import hashlib

from markupsafe import Markup

from odoo import api, fields, models
from odoo.exceptions import UserError
from odoo.tools.mail import html_normalize


def body_hash(fragments):
    """Empreinte stable d'un corps de document.

    `fragments` est une liste de tuples (code, html). Le HTML est normalisé
    avant hachage: rouvrir un document dans l'éditeur et le sauvegarder sans
    rien changer ne doit pas produire une nouvelle empreinte.
    """
    digest = hashlib.sha256()
    for code, html in fragments:
        digest.update((code or '').encode('utf-8'))
        digest.update(b'\x00')
        digest.update(section_hash(html).encode('utf-8'))
        digest.update(b'\x00')
    return digest.hexdigest()


def section_hash(html):
    """Empreinte d'un fragment HTML, insensible au bruit de l'éditeur."""
    if not html:
        return hashlib.sha256(b'').hexdigest()
    try:
        normalized = html_normalize(html) or ''
    except Exception:  # pragma: no cover - normalisation défensive
        normalized = html
    normalized = ' '.join(str(normalized).split())
    return hashlib.sha256(normalized.encode('utf-8')).hexdigest()


def is_blank_html(html):
    """Vrai si le fragment ne contient que du balisage vide."""
    if not html:
        return True
    from odoo.tools import html2plaintext
    text = html2plaintext(html or '').strip()
    if text:
        return False
    # Une image ou un tableau sans texte reste du contenu.
    return not any(tag in (html or '').lower() for tag in ('<img', '<table', '<iframe'))


class ProjectDocumentSection(models.Model):
    """Section du corps vivant d'un document (le brouillon de travail)."""
    _name = 'project.document.section'
    _description = 'Section de document'
    _order = 'sequence, id'

    document_id = fields.Many2one(
        'project.document',
        string='Document',
        required=True,
        ondelete='cascade',
        index=True,
    )
    template_line_id = fields.Many2one(
        'project.document.type.section',
        string='Ligne de gabarit',
        ondelete='set null',
    )
    code = fields.Char(
        string='Code',
        required=True,
        index=True,
        help="Clé d'appariement entre versions. Ne pas modifier une fois "
             "qu'une version a été gelée.",
    )
    name = fields.Char(
        string='Titre',
        required=True,
    )
    sequence = fields.Integer(
        string='Séquence',
        default=10,
    )
    content = fields.Html(
        string='Contenu',
        help='Corps de la section, tel qu’il paraîtra dans le document publié.',
    )
    content_kind = fields.Selection(
        selection=[
            ('html', 'Rédigée'),
            ('computed', 'Générée par le système'),
        ],
        string='Nature du contenu',
        default='html',
        required=True,
    )
    render_key = fields.Char(
        string='Clé de génération',
    )
    required = fields.Boolean(
        string='Obligatoire',
        default=False,
    )
    locked = fields.Boolean(
        string='Verrouillée',
        default=False,
    )
    page_break_before = fields.Boolean(
        string='Saut de page avant',
        default=False,
    )
    content_hash = fields.Char(
        string='Empreinte',
        compute='_compute_content_hash',
        store=True,
    )
    is_empty = fields.Boolean(
        string='Vide',
        compute='_compute_content_hash',
        store=True,
    )
    char_count = fields.Integer(
        string='Caractères',
        compute='_compute_content_hash',
        store=True,
    )
    company_id = fields.Many2one(
        related='document_id.company_id',
        string='Société',
        store=True,
        index=True,
    )

    _sql_constraints = [
        ('document_code_uniq', 'unique(document_id, code)',
         'Une section avec ce code existe déjà dans ce document!'),
    ]

    @api.depends('content')
    def _compute_content_hash(self):
        from odoo.tools import html2plaintext
        for rec in self:
            rec.content_hash = section_hash(rec.content)
            rec.is_empty = is_blank_html(rec.content)
            rec.char_count = len(html2plaintext(rec.content or '').strip())

    def write(self, vals):
        if 'code' in vals:
            frozen = self.env['project.document.version.section'].sudo().search_count([
                ('version_id.document_id', 'in', self.mapped('document_id').ids),
                ('code', 'in', self.mapped('code')),
            ])
            if frozen:
                raise UserError(
                    "Le code d'une section déjà gelée dans une version ne peut plus "
                    "changer: c'est la clé qui apparie les versions entre elles."
                )
        # `su` couvre les écritures système (gel de version, migrations, crons):
        # le verrou protège la saisie humaine, pas le module lui-même.
        if not self.env.su and not self.env.user.has_group(
                'project_knowledge_matrix.group_document_manager'):
            locked = self.filtered('locked')
            editable = {'sequence'}
            if locked and not set(vals) <= editable:
                raise UserError(
                    "Cette section est verrouillée. Seul un gestionnaire de documents "
                    "peut en modifier le contenu."
                )
        return super().write(vals)

    def _render_content(self):
        """Contenu final de la section, sections générées comprises."""
        self.ensure_one()
        if self.content_kind != 'computed':
            return Markup(self.content or '')
        return self.document_id._render_computed_section(self.render_key, self)

    def _snapshot_vals(self, version):
        """Valeurs de l'instantané figé pour cette section."""
        self.ensure_one()
        rendered = self._render_content()
        return {
            'version_id': version.id,
            'code': self.code,
            'name': self.name,
            'sequence': self.sequence,
            'content': str(rendered) if rendered else False,
            'content_kind': self.content_kind,
            'page_break_before': self.page_break_before,
        }
