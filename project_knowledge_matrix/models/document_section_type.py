from odoo import api, fields, models
from odoo.exceptions import UserError


class ProjectDocumentSectionType(models.Model):
    """Catalogue des sections disponibles pour le corps des documents.

    L'administrateur de l'instance décide ici quelles sections existent
    (Objectif, Portée, Politique, Gouvernance de cette documentation, ...).
    Le gabarit par type de document (project.document.type.section) choisit
    ensuite lesquelles s'appliquent à une Politique, une Procédure TI, etc.
    """
    _name = 'project.document.section.type'
    _description = 'Type de section de document'
    _order = 'sequence, name'

    name = fields.Char(
        string='Nom',
        required=True,
        translate=True,
        help='Titre affiché de la section dans le document et le PDF',
    )
    code = fields.Char(
        string='Code',
        required=True,
        index=True,
        help="Identifiant stable de la section. Sert de clé d'appariement "
             "entre deux versions: renommer le titre ne casse pas la comparaison, "
             "changer le code oui.",
    )
    sequence = fields.Integer(
        string='Séquence',
        default=10,
    )
    description = fields.Text(
        string='Description',
        translate=True,
        help="Ce que la section doit couvrir. Affiché comme aide à la rédaction.",
    )
    content_kind = fields.Selection(
        selection=[
            ('html', 'Rédigée'),
            ('computed', 'Générée par le système'),
        ],
        string='Nature du contenu',
        default='html',
        required=True,
        help="Rédigée: l'utilisateur écrit le texte. Générée: le module produit "
             "le contenu (historique des versions, gouvernance, approbations).",
    )
    render_key = fields.Char(
        string='Clé de génération',
        help="Pour une section générée: quel bloc produire "
             "(governance, revision_history, approvals).",
    )
    is_system = fields.Boolean(
        string='Section système',
        default=False,
        help='Les sections système sont fournies par le module et ne peuvent pas être supprimées.',
    )
    active = fields.Boolean(
        string='Actif',
        default=True,
    )

    _sql_constraints = [
        ('code_uniq', 'unique(code)', 'Le code de section doit être unique!'),
    ]

    @api.constrains('content_kind', 'render_key')
    def _check_render_key(self):
        for rec in self:
            if rec.content_kind == 'computed' and not rec.render_key:
                raise UserError(
                    "Une section générée par le système doit avoir une clé de génération."
                )

    @api.ondelete(at_uninstall=False)
    def _unlink_except_system(self):
        for rec in self:
            if rec.is_system:
                raise UserError(
                    f"La section « {rec.name} » est fournie par le module. "
                    "Désactivez-la plutôt que de la supprimer."
                )

    @api.depends('code', 'name')
    def _compute_display_name(self):
        for rec in self:
            rec.display_name = f"[{rec.code}] {rec.name}" if rec.code else rec.name
