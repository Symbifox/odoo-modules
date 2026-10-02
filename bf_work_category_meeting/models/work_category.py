from odoo import models


class MeetingRecord(models.Model):
    _name = "meeting.record"
    _inherit = ["meeting.record", "bf.work.category.mixin"]

    _bf_work_category_sources = (("record", "project_id"),)
