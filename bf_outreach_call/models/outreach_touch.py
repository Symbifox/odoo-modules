# -*- coding: utf-8 -*-
from odoo import fields, models


class BfOutreachTouch(models.Model):
    _inherit = "bf.outreach.touch"

    call_archive_id = fields.Many2one(
        "call.archive.call",
        string="Source call",
        ondelete="set null",
        copy=False,
        index="btree_not_null",
        help="Archived call this interaction was inferred from. Also acts "
             "as a safeguard: the same call is never matched twice.",
    )
