from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class EmployerScope(models.AbstractModel):
    """La portée d'un objet de l'employeur : l'unité si une convention s'en
    mêle, la société sinon.

    🔴 Une société sans syndicat a quand même des obligations légales
    (affichage des normes du travail, politique de harcèlement, équité
    salariale), un comité de santé et de sécurité et des affichages internes.
    Exiger une unité la forçait à inventer un syndicat pour s'en servir. L'unité
    est donc facultative, et c'est la SOCIÉTÉ qui porte la portée, règles
    d'enregistrement multi-société comprises.

    Quand une unité est choisie, la société la suit, et une garde refuse
    qu'elles divergent : un objet rattaché à une unité vit dans la société de
    l'unité, comme avant.

    ⚠️ `company_id` n'a PAS de `default=` : un défaut sur un champ calculé
    éditable passe avant le calcul, et une obligation créée avec l'unité d'une
    autre société que la société active prendrait la société active, puis
    tomberait sur la garde. Le repli sur la société active se fait dans le
    calcul, seulement quand il n'y a pas d'unité.
    """

    _name = "bf.labour.employer.scope"
    _description = "Portée employeur : unité ou société"

    unit_id = fields.Many2one(
        "bf.labour.unit", string="Unité de négociation",
        ondelete="cascade", index=True,
        help="Laissée vide, l'objet vise toute la société, syndiquée ou non.",
    )
    company_id = fields.Many2one(
        "res.company", string="Société", required=True, index=True,
        compute="_compute_company_id", store=True, readonly=False,
        precompute=True,
        # Un champ calculé ne se recopie pas par défaut. Sans ceci, la
        # reconduction d'une obligation sans unité tomberait dans la société
        # ACTIVE de la personne qui la marque faite, pas dans la sienne.
        copy=True,
        help="Suit l'unité quand il y en a une. Sans unité, c'est elle qui dit "
             "à quelle société l'objet appartient.",
    )

    @api.depends("unit_id.company_id")
    def _compute_company_id(self):
        for record in self:
            if record.unit_id:
                record.company_id = record.unit_id.company_id
            elif not record.company_id:
                record.company_id = self.env.company

    @api.constrains("unit_id", "company_id")
    def _check_unit_company(self):
        for record in self:
            if record.unit_id and record.unit_id.company_id != record.company_id:
                raise ValidationError(_(
                    "L'unité « %(unite)s » appartient à %(sienne)s, pas à "
                    "%(autre)s. Un objet rattaché à une unité vit dans la "
                    "société de l'unité ; sans unité, il vise la société choisie.",
                    unite=record.unit_id.display_name,
                    sienne=record.unit_id.company_id.display_name,
                    autre=record.company_id.display_name,
                ))
