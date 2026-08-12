from odoo import api, fields, models


class DocumentSoftware(models.Model):
    """Software catalog for document tracking.

    This model provides software tracking when the hosting_management module
    is not installed. If hosting_management is installed, documents can
    optionally link to hosting.software instead.
    """
    _name = 'document.software'
    _description = 'Logiciel documenté'
    _order = 'name'

    name = fields.Char(
        string='Nom',
        required=True,
    )
    code = fields.Char(
        string='Code',
        help='Code court unique (ex: NC pour Nextcloud)',
    )
    description = fields.Text(
        string='Description',
    )
    website = fields.Char(
        string='Site web',
    )
    current_version = fields.Char(
        string='Version actuelle',
        help='Dernière version connue du logiciel',
    )
    version_date = fields.Date(
        string='Date de version',
        help='Date de publication de la version actuelle',
    )
    icon = fields.Char(
        string='Icône',
        default='fa-cube',
        help='Classe Font Awesome (ex: fa-cloud, fa-database)',
    )
    color = fields.Integer(
        string='Couleur',
        default=0,
    )
    active = fields.Boolean(
        string='Actif',
        default=True,
    )

    # Computed fields
    document_count = fields.Integer(
        string='Documents',
        compute='_compute_document_count',
    )

    _sql_constraints = [
        ('code_uniq', 'unique(code)', 'Le code logiciel doit être unique!'),
    ]

    def _compute_document_count(self):
        Document = self.env['project.document']
        for software in self:
            software.document_count = Document.search_count([
                ('software_id', '=', software.id),
            ])

    def action_view_documents(self):
        """Open documents for this software."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': f'Documents - {self.name}',
            'res_model': 'project.document',
            'view_mode': 'list,kanban,form',
            'domain': [('software_id', '=', self.id)],
            'context': {
                'default_software_id': self.id,
            },
        }


class DocumentSoftwareVersion(models.Model):
    """Track specific software versions for documentation purposes."""
    _name = 'document.software.version'
    _description = 'Version de logiciel'
    _order = 'software_id, version desc'

    software_id = fields.Many2one(
        'document.software',
        string='Logiciel',
        required=True,
        ondelete='cascade',
    )
    version = fields.Char(
        string='Version',
        required=True,
    )
    release_date = fields.Date(
        string='Date de publication',
    )
    is_lts = fields.Boolean(
        string='LTS',
        help='Version à support long terme (Long Term Support)',
    )
    support_status = fields.Selection([
        ('current', 'Actuelle'),
        ('supported', 'Supportée'),
        ('security', 'Correctifs sécurité uniquement'),
        ('deprecated', 'Obsolète'),
        ('eol', 'Fin de vie'),
    ], string='Statut de support', default='current')
    notes = fields.Text(
        string='Notes',
        help='Notes de version ou remarques importantes',
    )
    active = fields.Boolean(
        string='Actif',
        default=True,
    )

    # Computed
    display_name = fields.Char(
        string='Nom affiché',
        compute='_compute_display_name',
        store=True,
    )

    _sql_constraints = [
        ('version_software_uniq', 'unique(software_id, version)',
         'Cette version existe déjà pour ce logiciel!'),
    ]

    @api.depends('software_id.name', 'version')
    def _compute_display_name(self):
        for record in self:
            if record.software_id and record.version:
                record.display_name = f"{record.software_id.name} {record.version}"
            else:
                record.display_name = record.version or ''
