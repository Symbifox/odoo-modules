from datetime import timedelta

from odoo import _, api, models

from odoo.addons.bf_work_hours.models.engine import Item
from odoo.addons.bf_work_hours.models.work_collector import MEETING_MAX


class WorkCollector(models.AbstractModel):
    _inherit = "bf.work.collector"

    @api.model
    def _bf_source_descriptions(self):
        return super()._bf_source_descriptions() + [
            ("meeting_report", _("Meeting reports"),
             _("Start and length of the meetings where you are marked present, by yourself or by the "
               "processing of the recording. Not what was said.")),
        ]

    @api.model
    def _bf_collect(self, user, start, stop):
        items = super()._bf_collect(user, start, stop)
        # 🔴 Anyone who can write meeting reports can mark someone present, stretch
        # the meeting, or name someone organizer: only a presence noted by the
        # person, Odoo or the processing counts, for the hours the meeting had
        # then (see meeting_attendance.py). No presence, no meeting.
        lines = self.env["meeting.attendance"].sudo().search([
            ("partner_id", "=", user.partner_id.id),
            ("status", "=", "present"),
            ("bf_work_status_by", "=", "trusted"),
            ("meeting_id.active", "=", True),  # an archived report (a call set aside) is not a meeting
            ("meeting_id.date", ">=", start - MEETING_MAX),
            ("meeting_id.date", "<", stop),
        ])
        for line in lines:
            record = line.meeting_id
            if not record.duration_minutes:
                continue
            length = timedelta(minutes=record.duration_minutes)
            if length >= MEETING_MAX:
                continue
            begin, end = record.date, record.date + length
            if line.bf_work_status_start:
                noted_end = line.bf_work_status_start + timedelta(minutes=line.bf_work_status_minutes or 0)
                begin, end = max(begin, line.bf_work_status_start), min(end, noted_end)
            if end > begin:
                items.append(Item(begin, end, "meeting_report", "meeting"))
        return items
