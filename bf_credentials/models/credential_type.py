from odoo import fields, models


class ProjectCredentialType(models.Model):
    """Modèle de données de référence pour les catégories d'identifiants."""
    _name = 'project.credential.type'
    _description = "Credential type"
    _order = 'sequence, name'

    name = fields.Char(
        string="Name",
        required=True,
        translate=True,
    )
    code = fields.Char(
        string='Code',
        required=True,
        help="Unique code for this credential type",
    )
    sequence = fields.Integer(
        string="Sequence",
        default=10,
    )
    icon = fields.Char(
        string="Icon",
        default='fa-key',
        help="Font Awesome class (e.g. fa-envelope, fa-server)",
    )
    color = fields.Integer(
        string="Color",
        default=0,
    )
    description = fields.Text(
        string='Description',
        translate=True,
    )
    active = fields.Boolean(
        string="Active",
        default=True,
    )

    # Bascules de visibilité des champs
    show_domain = fields.Boolean(
        string="Show domain field",
        default=False,
        help="Show the domain field for credentials of this type",
    )
    show_url = fields.Boolean(
        string="Show URL field",
        default=True,
        help="Show the URL field for credentials of this type",
    )
    show_api_key = fields.Boolean(
        string="Show API key field",
        default=False,
        help="Show the API key field for credentials of this type",
    )
    show_key_file = fields.Boolean(
        string="Show key file field",
        default=False,
        help="Show the key file upload field for credentials of this type",
    )

    _sql_constraints = [
        ('code_uniq', 'unique(code)', "The code must be unique!"),
    ]
