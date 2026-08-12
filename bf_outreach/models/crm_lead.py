# -*- coding: utf-8 -*-
from odoo import fields, models


class CrmLead(models.Model):
    _inherit = "crm.lead"

    outreach_target_id = fields.Many2one(
        "bf.outreach.target",
        string="Cible de démarchage",
        copy=False,
        index="btree_not_null",
        help="Cible dont cette opportunité est issue.",
    )
    outreach_campaign_id = fields.Many2one(
        "bf.outreach.campaign",
        string="Campagne de démarchage",
        related="outreach_target_id.campaign_id",
        store=True,
        index="btree_not_null",
        help="Permet de mesurer ce que le démarchage rapporte au pipeline.",
    )
