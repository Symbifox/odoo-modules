from odoo import _, fields, models


class CallArchiveCall(models.Model):
    _inherit = 'call.archive.call'

    meeting_record_ids = fields.One2many(
        'meeting.record',
        'call_archive_id',
        string="Linked meeting reports",
    )
    meeting_record_count = fields.Integer(
        compute='_compute_meeting_record_count',
    )

    def _compute_meeting_record_count(self):
        for call in self:
            call.meeting_record_count = len(call.meeting_record_ids)

    def action_view_meeting_records(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _("Linked meeting reports"),
            'res_model': 'meeting.record',
            'view_mode': 'list,form',
            'domain': [('call_archive_id', '=', self.id)],
            'context': {'default_call_archive_id': self.id,
                        'default_meeting_type': 'phone'},
        }
