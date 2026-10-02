from odoo import models


class MeetingAgenda(models.Model):
    _name = "meeting.agenda"
    _inherit = ["meeting.agenda", "bf.color.mixin"]


class MeetingRecord(models.Model):
    _name = "meeting.record"
    _inherit = ["meeting.record", "bf.color.mixin"]
