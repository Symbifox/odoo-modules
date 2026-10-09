# -*- coding: utf-8 -*-
from odoo import fields, models


class BfOutreachTarget(models.Model):
    _inherit = "bf.outreach.target"

    booking_url = fields.Char(
        string="Booking link",
        related="campaign_id.booking_type_id.public_url",
        readonly=True,
        help="To drop into the outreach email: the target picks their own "
             "slot, and the booking moves their file along by itself.",
    )
