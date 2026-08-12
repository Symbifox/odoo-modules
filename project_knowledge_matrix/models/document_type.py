from odoo import fields, models


class ProjectDocumentType(models.Model):
    """Reference data model for document categories."""
    _name = 'project.document.type'
    _description = 'Type de document'
    _order = 'sequence, name'

    name = fields.Char(
        string='Nom',
        required=True,
        translate=True,
    )
    code = fields.Char(
        string='Code',
        required=True,
        help='Identifiant unique pour ce type de document',
    )
    sequence = fields.Integer(
        string='Séquence',
        default=10,
    )
    icon = fields.Char(
        string='Icône',
        default='fa-file',
        help='Classe d\'icône Font Awesome (ex: fa-book, fa-gavel)',
    )
    color = fields.Integer(
        string='Couleur',
        default=0,
    )
    description = fields.Text(
        string='Description',
        translate=True,
    )
    is_internal = fields.Boolean(
        string='Document interne',
        default=False,
        help='Cochez pour les documents internes (politiques, procédures RH, etc.)',
    )
    active = fields.Boolean(
        string='Actif',
        default=True,
    )

    _sql_constraints = [
        ('code_uniq', 'unique(code)', 'Le code doit être unique!'),
    ]
