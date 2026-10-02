from odoo import models


class BfOutreachTarget(models.Model):
    _name = "bf.outreach.target"
    _inherit = ["bf.outreach.target", "bf.color.mixin"]


class BfOutreachCampaign(models.Model):
    _name = "bf.outreach.campaign"
    _inherit = ["bf.outreach.campaign", "bf.color.mixin"]


class BfOutreachTag(models.Model):
    _name = "bf.outreach.tag"
    _inherit = ["bf.outreach.tag", "bf.color.mixin"]
