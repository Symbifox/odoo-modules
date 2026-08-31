from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class CorporateResolutionSignatory(models.Model):
    """Qui signe une résolution, et en quelle qualité.

    Le registre des administrateurs ne répond pas à cette question. Une
    résolution des actionnaires est signée par des actionnaires; une
    dénonciation d'intérêt est contresignée par un dirigeant qui ne vote pas.
    Déduire la qualité du registre revient à l'affirmer au hasard — d'où une
    ligne par signataire, saisie à la main.
    """

    _name = 'corporate.resolution.signatory'
    _description = 'Signataire de résolution corporative'
    _order = 'sequence, id'

    resolution_id = fields.Many2one(
        'corporate.resolution',
        string='Résolution',
        required=True,
        ondelete='cascade',
        index=True,
    )
    sequence = fields.Integer(
        string='Ordre',
        default=10,
        help="Ordre d'impression des blocs de signature.",
    )
    partner_id = fields.Many2one(
        'res.partner',
        string='Signataire',
        required=True,
    )
    capacity = fields.Selection(
        selection=[
            ('sole_shareholder', 'Actionnaire unique'),
            ('shareholder', 'Actionnaire'),
            ('sole_director', 'Administrateur unique'),
            ('director', 'Administrateur'),
            ('officer', 'Dirigeant'),
            ('proxy', 'Fondé de pouvoir'),
            ('other', 'Autre'),
        ],
        string='Qualité',
        required=True,
        default='shareholder',
        help="Qualité en laquelle la personne signe. C'est elle qui est "
             "imprimée sous le nom.",
    )
    capacity_custom = fields.Char(
        string='Qualité (texte)',
        help="Qualité littérale, quand la liste ne suffit pas — par exemple "
             "« Vice-président, secrétaire et trésorier ». Obligatoire quand "
             "la qualité est « Autre ».",
    )
    capacity_label = fields.Char(
        string='Qualité imprimée',
        compute='_compute_capacity_label',
    )
    purpose = fields.Char(
        string='Fins de la signature',
        help="Mention imprimée sous la qualité quand la signature est donnée "
             "à une fin limitée — par exemple « aux seules fins d'attester la "
             "dénonciation d'intérêt ».",
    )

    @api.depends('capacity', 'capacity_custom')
    def _compute_capacity_label(self):
        libelles = dict(
            self.fields_get(['capacity'])['capacity']['selection']
        )
        for rec in self:
            if rec.capacity == 'other':
                rec.capacity_label = rec.capacity_custom or False
            else:
                rec.capacity_label = libelles.get(rec.capacity) or False

    @api.constrains('capacity', 'capacity_custom')
    def _check_capacity_custom(self):
        """« Autre » sans texte imprimerait un nom sans qualité.

        Le défaut que la fiche corrige est justement une qualité manquante ou
        fausse sous un nom : la laisser vide par accident le réintroduit.
        """
        for rec in self:
            if rec.capacity == 'other' and not rec.capacity_custom:
                raise ValidationError(_(
                    "Précisez la qualité de %s : « Autre » sans texte "
                    "imprimerait un nom sans qualité."
                ) % (rec.partner_id.name or _('ce signataire')))
