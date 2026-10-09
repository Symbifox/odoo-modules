# -*- coding: utf-8 -*-
from odoo import fields, models


class BfOutreachCampaign(models.Model):
    _inherit = "bf.outreach.campaign"

    booking_type_id = fields.Many2one(
        "resource.booking.type",
        string="Booking type",
        help="Type offered to this campaign's targets. Its public link is "
             "available on every target, ready to drop into the email "
             "template.",
    )
