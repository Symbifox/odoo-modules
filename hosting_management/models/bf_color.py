from odoo import models


class HostingMaintenanceSchedule(models.Model):
    _name = "hosting.maintenance.schedule"
    _inherit = ["hosting.maintenance.schedule", "bf.color.mixin"]


class HostingEndpointGroup(models.Model):
    _name = "hosting.endpoint.group"
    _inherit = ["hosting.endpoint.group", "bf.color.mixin"]


class HostingServiceTag(models.Model):
    _name = "hosting.service.tag"
    _inherit = ["hosting.service.tag", "bf.color.mixin"]
