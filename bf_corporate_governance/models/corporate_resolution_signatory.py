from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class CorporateResolutionSignatory(models.Model):
    """Qui signe une résolution, et en quelle qualité.

    Le registre des administrateurs ne répond pas à cette question. Une
    résolution des actionnaires est signée par des actionnaires; une
    dénonciation d'intérêt est contresignée par un dirigeant qui ne vote pas;
    une résolution des membres l'est par la présidence et le secrétariat de
    l'assemblée. Déduire la qualité du registre revient à l'affirmer au hasard,
    d'où une ligne par signataire, saisie à la main.

    La qualité d'office est « Actionnaire », sauf sur une résolution des
    membres : la fiche n'en propose pas, et la ligne exige qu'on la choisisse.
    Une résolution des membres ne se signe jamais en qualité d'actionnaire, et
    c'est une contrainte, pas seulement la fiche : une ligne créée par un appel
    RPC ou un import prendrait sinon « Actionnaire » d'office, comme une ligne
    déplacée d'une autre résolution. Un « Actionnaire » imprimé sous le nom de
    la présidence d'une assemblée d'OBNL serait faux.
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
            ('assembly_chair', "Présidence d'assemblée"),
            ('assembly_secretary', "Secrétariat d'assemblée"),
            ('other', 'Autre'),
        ],
        string='Qualité',
        required=True,
        default=lambda self: self._default_capacity(),
        help="Qualité en laquelle la personne signe. C'est elle qui est "
             "imprimée sous le nom. « Actionnaire » d'office, sauf sur une "
             "résolution des membres, où elle se choisit (présidence ou "
             "secrétariat d'assemblée, le plus souvent).",
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

    @api.model
    def _default_capacity(self):
        """« Actionnaire » d'office, sauf quand la ligne s'ajoute à une
        résolution des membres (la fiche passe son type dans le contexte)."""
        if self.env.context.get('resolution_type') == 'members':
            return False
        return 'shareholder'

    @api.constrains('capacity', 'resolution_id')
    def _check_members_capacity(self):
        """Une résolution des membres ne se signe pas « Actionnaire ».

        La qualité se choisit à la création d'une ligne ; cette contrainte
        tient aussi quand une ligne change de résolution, ou quand une
        résolution devient une résolution des membres (voir la résolution).
        """
        self.resolution_id._check_members_signatories()

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
