from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    default_privacy_framework_id = fields.Many2one(
        comodel_name="privacy.framework",
        string="Cadre de confidentialité par défaut",
        domain="['|', ('company_id', '=', False), ('company_id', '=', id)]",
        help="Cadre réglementaire appliqué par défaut aux nouveaux "
        "enregistrements de vie privée de cette société.",
    )
