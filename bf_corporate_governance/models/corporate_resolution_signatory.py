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
    _description = "Corporate resolution signatory"
    _order = 'sequence, id'

    resolution_id = fields.Many2one(
        'corporate.resolution',
        string="Resolution",
        required=True,
        ondelete='cascade',
        index=True,
    )
    sequence = fields.Integer(
        string="Order",
        default=10,
        help="Print order of the signature blocks.",
    )
    partner_id = fields.Many2one(
        'res.partner',
        string="Signatory",
        required=True,
    )
    capacity = fields.Selection(
        selection=[
            ('sole_shareholder', "Sole shareholder"),
            ('shareholder', "Shareholder"),
            ('sole_director', "Sole director"),
            ('director', "Director"),
            ('officer', "Officer"),
            ('proxy', "Proxy holder"),
            ('assembly_chair', "Meeting chair"),
            ('assembly_secretary', "Meeting secretary"),
            ('other', "Other"),
        ],
        string="Capacity",
        required=True,
        default=lambda self: self._default_capacity(),
        help="Capacity in which the person signs. It is what is printed "
             "under the name. \"Shareholder\" by default, except on a "
             "members' resolution, where it must be chosen (meeting chair "
             "or meeting secretary, most often).",
    )
    capacity_custom = fields.Char(
        string="Capacity (text)",
        help="Literal capacity, when the list is not enough, for example "
             "\"Vice-president, secretary and treasurer\". Required when "
             "the capacity is \"Other\".",
    )
    capacity_label = fields.Char(
        string="Printed capacity",
        compute='_compute_capacity_label',
    )
    purpose = fields.Char(
        string="Signature purpose",
        help="Note printed under the capacity when the signature is given "
             "for a limited purpose, for example \"for the sole purpose "
             "of attesting the disclosure of interest\".",
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
    @api.depends_context('lang')
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
                raise ValidationError(self.env._(
                    "Specify the capacity of %s: \"Other\" without text "
                    "would print a name with no capacity.",
                    rec.partner_id.name or self.env._("this signatory")))
