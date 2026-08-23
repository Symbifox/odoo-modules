"""Ce que l'assemblée a besoin de savoir du syndicat.

Le socle porte la structure : fractions, quotes-parts, registre des
copropriétaires. Deux données de plus servent uniquement au calcul des voix, et
vivent donc ici plutôt que dans le socle : qui est le promoteur, et quelle
fraction il occupe (art. 1092 et 1093 C.c.Q.).

La date d'inscription de la déclaration, elle, existe déjà au socle sous le nom
`declaration_date` : c'est la date de publication au registre foncier, celle
dont l'art. 1092 fait courir ses délais.
"""
from odoo import fields, models


class BfPropertySyndicat(models.Model):
    _inherit = "bf.property.syndicat"

    assembly_ids = fields.One2many(
        "bf.property.assembly", "syndicat_id", string="Assemblées"
    )
    promoter_partner_id = fields.Many2one(
        "res.partner",
        string="Promoteur",
        tracking=True,
        help="Art. 1093 C.c.Q. : celui qui, au moment de l'inscription de la "
             "déclaration, était propriétaire d'au moins la moitié des "
             "fractions, ou ses ayants cause. Renseigné à la main : la qualité "
             "de promoteur se constate au titre, pas au registre courant.",
    )
    promoter_unit_id = fields.Many2one(
        "bf.property.unit",
        string="Fraction occupée par le promoteur",
        domain="[('syndicat_id', '=', id)]",
        help="Art. 1092 C.c.Q. : le plafond porte sur les voix du promoteur "
             "« outre les voix attachées à la fraction qu'il occupe ». Cette "
             "fraction-là échappe donc au plafond.",
    )
