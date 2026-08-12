# -*- coding: utf-8 -*-
from odoo import fields, models


class BfOutreachTouch(models.Model):
    _inherit = "bf.outreach.touch"

    bf_email_id = fields.Many2one(
        "bf.email",
        string="Courriel d'origine",
        ondelete="set null",
        copy=False,
        index="btree_not_null",
        help="Courriel archivé dont cette interaction a été déduite. Sert aussi "
        "de garde-fou : un même courriel n'est jamais rapproché deux fois.",
    )
