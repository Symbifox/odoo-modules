from odoo import fields, models


class ProjectCredentialType(models.Model):
    """Modèle de données de référence pour les catégories d'identifiants."""
    _name = 'project.credential.type'
    _description = "Type d'identifiant"
    _order = 'sequence, name'

    name = fields.Char(
        string='Nom',
        required=True,
        translate=True,
    )
    code = fields.Char(
        string='Code',
        required=True,
        help="Identifiant unique pour ce type d'identifiant",
    )
    sequence = fields.Integer(
        string='Séquence',
        default=10,
    )
    icon = fields.Char(
        string='Icône',
        default='fa-key',
        help='Classe Font Awesome (ex. : fa-envelope, fa-server)',
    )
    color = fields.Integer(
        string='Couleur',
        default=0,
    )
    description = fields.Text(
        string='Description',
        translate=True,
    )
    active = fields.Boolean(
        string='Actif',
        default=True,
    )

    # Bascules de visibilité des champs
    show_domain = fields.Boolean(
        string='Afficher le champ domaine',
        default=False,
        help="Afficher le champ domaine pour les identifiants de ce type",
    )
    show_url = fields.Boolean(
        string='Afficher le champ URL',
        default=True,
        help="Afficher le champ URL pour les identifiants de ce type",
    )
    show_api_key = fields.Boolean(
        string='Afficher le champ clé API',
        default=False,
        help="Afficher le champ clé API pour les identifiants de ce type",
    )
    show_key_file = fields.Boolean(
        string='Afficher le champ fichier de clé',
        default=False,
        help="Afficher le champ de téléversement de fichier de clé pour les identifiants de ce type",
    )

    _sql_constraints = [
        ('code_uniq', 'unique(code)', 'Le code doit être unique !'),
    ]
