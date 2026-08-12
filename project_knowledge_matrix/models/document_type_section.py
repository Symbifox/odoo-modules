from odoo import api, fields, models


class ProjectDocumentTypeSection(models.Model):
    """Ligne de gabarit: quelles sections composent un type de document.

    C'est ici que « Politique » diffère de « Procédure TI ». Un type de
    document SANS ligne de gabarit ne reçoit pas de corps interne: son onglet
    Corps reste masqué et il continue de pointer vers un fichier externe.
    """
    _name = 'project.document.type.section'
    _description = 'Section de gabarit de type de document'
    _order = 'sequence, id'

    document_type_id = fields.Many2one(
        'project.document.type',
        string='Type de document',
        required=True,
        ondelete='cascade',
        index=True,
    )
    section_type_id = fields.Many2one(
        'project.document.section.type',
        string='Section',
        required=True,
        ondelete='restrict',
    )
    name = fields.Char(
        string='Titre',
        help="Laisser vide pour reprendre le nom du type de section.",
    )
    sequence = fields.Integer(
        string='Séquence',
        default=10,
    )
    required = fields.Boolean(
        string='Obligatoire',
        default=False,
        help='Une section obligatoire doit être remplie avant de geler une version.',
    )
    locked = fields.Boolean(
        string='Verrouillée',
        default=False,
        help='Section que seul un gestionnaire de documents peut modifier.',
    )
    page_break_before = fields.Boolean(
        string='Saut de page avant',
        default=False,
        help='Force un saut de page avant cette section dans le PDF.',
    )
    default_content = fields.Html(
        string='Contenu par défaut',
        help="Texte pré-rempli à la création d'un document de ce type.",
    )
    content_kind = fields.Selection(
        related='section_type_id.content_kind',
        string='Nature du contenu',
        readonly=True,
    )

    _sql_constraints = [
        ('type_section_uniq', 'unique(document_type_id, section_type_id)',
         'Cette section est déjà dans le gabarit de ce type de document!'),
    ]

    def _section_title(self):
        self.ensure_one()
        return self.name or self.section_type_id.name

    def _section_vals(self, document):
        """Valeurs de création d'une section vivante à partir de cette ligne."""
        self.ensure_one()
        return {
            'document_id': document.id,
            'template_line_id': self.id,
            'code': self.section_type_id.code,
            'name': self._section_title(),
            'sequence': self.sequence,
            'content': self.default_content or False,
            'content_kind': self.section_type_id.content_kind,
            'render_key': self.section_type_id.render_key,
            'required': self.required,
            'locked': self.locked,
            'page_break_before': self.page_break_before,
        }


class ProjectDocumentTypeBody(models.Model):
    _inherit = 'project.document.type'

    section_line_ids = fields.One2many(
        'project.document.type.section',
        'document_type_id',
        string='Sections du gabarit',
    )
    section_line_count = fields.Integer(
        string='Sections',
        compute='_compute_section_line_count',
    )
    supports_body = fields.Boolean(
        string='Corps interne possible',
        compute='_compute_section_line_count',
        store=True,
        help="Vrai dès que le type a au moins une section de gabarit. "
             "Sans gabarit, les documents de ce type restent des pointeurs "
             "vers un fichier externe.",
    )

    @api.depends('section_line_ids')
    def _compute_section_line_count(self):
        for rec in self:
            rec.section_line_count = len(rec.section_line_ids)
            rec.supports_body = bool(rec.section_line_ids)
