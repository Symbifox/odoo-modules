from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    # Par société : chaque organisation décide qui, en plus de la gestion des
    # pastilles, peut associer une étiquette au premier scan.
    bf_qr_groupe_ids = fields.Many2many(
        "res.groups", "bf_qr_company_group_rel", "company_id", "group_id",
        string="Groupes qui associent les étiquettes QR",
        help="En plus de la gestion des pastilles, qui le peut toujours.",
    )
