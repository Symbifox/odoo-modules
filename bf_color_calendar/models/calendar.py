from odoo import models


class CalendarEventType(models.Model):
    _name = "calendar.event.type"
    _inherit = ["calendar.event.type", "bf.color.mixin"]
