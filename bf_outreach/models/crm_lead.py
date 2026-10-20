# -*- coding: utf-8 -*-
from odoo import fields, models


class CrmLead(models.Model):
    _inherit = "crm.lead"

    outreach_target_id = fields.Many2one(
        "bf.outreach.target",
        string="Outreach target",
        copy=False,
        index="btree_not_null",
        help="Target this opportunity came from.",
    )
    outreach_campaign_id = fields.Many2one(
        "bf.outreach.campaign",
        string="Outreach campaign",
        related="outreach_target_id.campaign_id",
        store=True,
        index="btree_not_null",
        help="Makes it possible to measure what outreach brings to the "
             "pipeline.",
    )
