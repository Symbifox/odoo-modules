from odoo import models


class CrmTag(models.Model):
    _name = "crm.tag"
    _inherit = ["crm.tag", "bf.color.mixin"]


class CrmLead(models.Model):
    _name = "crm.lead"
    _inherit = ["crm.lead", "bf.color.mixin"]
