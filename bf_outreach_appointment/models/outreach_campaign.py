# -*- coding: utf-8 -*-
from odoo import fields, models


class BfOutreachCampaign(models.Model):
    _inherit = "bf.outreach.campaign"

    booking_type_id = fields.Many2one(
        "resource.booking.type",
        string="Type de rendez-vous",
        help="Type proposé aux cibles de cette campagne. Son lien public est "
        "disponible sur chaque cible, pour le glisser dans le modèle de courriel.",
    )
