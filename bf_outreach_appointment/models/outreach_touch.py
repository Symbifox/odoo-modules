# -*- coding: utf-8 -*-
from odoo import fields, models


class BfOutreachTouch(models.Model):
    _inherit = "bf.outreach.touch"

    booking_id = fields.Many2one(
        "resource.booking",
        string="Booking",
        ondelete="set null",
        copy=False,
        index="btree_not_null",
        help="Booking this interaction was inferred from. Also acts as a "
             "safeguard: the same booking is never logged twice.",
    )
