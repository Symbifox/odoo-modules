# -*- coding: utf-8 -*-
from odoo import fields, models


class BfOutreachTouch(models.Model):
    _inherit = "bf.outreach.touch"

    booking_id = fields.Many2one(
        "resource.booking",
        string="Rendez-vous",
        ondelete="set null",
        copy=False,
        index="btree_not_null",
        help="Réservation dont cette interaction a été déduite. Sert aussi de "
        "garde-fou : un même rendez-vous n'est jamais journalisé deux fois.",
    )
