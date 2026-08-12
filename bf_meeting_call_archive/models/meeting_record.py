from odoo import api, fields, models


class MeetingRecord(models.Model):
    _inherit = 'meeting.record'

    call_archive_id = fields.Many2one(
        'call.archive.call',
        string='Appel archivé',
        index=True,
        ondelete='set null',
        help='Appel téléphonique archivé promu en compte rendu. '
             'Pré-remplit la durée et le client à la sélection.',
    )

    @api.onchange('call_archive_id')
    def _onchange_call_archive_id(self):
        for record in self:
            call = record.call_archive_id
            if not call:
                continue
            record.meeting_type = 'phone'
            if call.duration and not record.duration_minutes:
                record.duration_minutes = max(1, round(call.duration / 60))
            if not record.date and call.date:
                record.date = call.date
            thread = call.thread_id
            if thread and thread.partner_id and not record.partner_id \
                    and not record.project_id:
                # partner_id is related on project_id; only auto-fill the
                # participant if the meeting isn't bound to a project yet.
                if thread.partner_id not in record.participant_ids:
                    record.participant_ids = [(4, thread.partner_id.id)]
