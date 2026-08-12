# -*- coding: utf-8 -*-
from odoo import fields, models


class BfOutreachTouch(models.Model):
    _inherit = "bf.outreach.touch"

    call_archive_id = fields.Many2one(
        "call.archive.call",
        string="Appel d'origine",
        ondelete="set null",
        copy=False,
        index="btree_not_null",
        help="Appel archivé dont cette interaction a été déduite. Sert aussi de "
        "garde-fou : un même appel n'est jamais rapproché deux fois.",
    )
