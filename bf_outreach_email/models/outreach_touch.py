# -*- coding: utf-8 -*-
from odoo import fields, models


class BfOutreachTouch(models.Model):
    _inherit = "bf.outreach.touch"

    bf_email_id = fields.Many2one(
        "bf.email",
        string="Source email",
        ondelete="set null",
        copy=False,
        index="btree_not_null",
        help="Archived email this interaction was inferred from. Also "
             "acts as a safeguard: the same email is never matched twice.",
    )
