# -*- coding: utf-8 -*-
from odoo import fields, models


class BfOutreachTarget(models.Model):
    _inherit = "bf.outreach.target"

    booking_url = fields.Char(
        string="Lien de prise de rendez-vous",
        related="campaign_id.booking_type_id.public_url",
        readonly=True,
        help="À insérer dans le courriel de démarchage : la cible choisit "
        "elle-même sa plage, et le rendez-vous pris fait avancer son dossier.",
    )
