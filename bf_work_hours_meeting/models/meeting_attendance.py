from odoo import SUPERUSER_ID, api, fields, models

from odoo.addons.bf_work_hours.models import tools
from odoo.addons.bf_work_hours.models.tools import impersonating

PROCESSING_PARAM = "bf_work_hours_meeting.processing_logins"
STAMP_FIELDS = ("bf_work_status_by", "bf_work_status_uid", "bf_work_status_start", "bf_work_status_minutes")


class MeetingAttendance(models.Model):
    """Who marked the person present, and for which hours of the meeting.

    Anyone who can write meeting reports can mark someone present and stretch
    the meeting afterwards: the presence counts only if the person, Odoo or the
    meeting processing wrote it, and only for the hours the meeting had then.
    """

    _inherit = "meeting.attendance"

    bf_work_status_by = fields.Selection(
        [("trusted", "The person, Odoo or the meeting processing"), ("other", "Someone else")],
        string="Presence noted by", readonly=True, copy=False)
    # Who noted it: ``write_uid`` is rewritten by any later recomputation of the line.
    bf_work_status_uid = fields.Many2one("res.users", string="Presence noted by (account)", readonly=True,
                                         copy=False, ondelete="set null")
    bf_work_status_start = fields.Datetime(string="Presence for (start)", readonly=True, copy=False)
    bf_work_status_minutes = fields.Integer(string="Presence for (minutes)", readonly=True, copy=False)

    @api.model
    def _bf_work_processing_ids(self):
        logins = [x.strip() for x in (self.env["ir.config_parameter"].sudo().get_param(PROCESSING_PARAM) or "")
                  .split(",") if x.strip()]
        return set(self.env["res.users"].sudo().with_context(active_test=False).search([("login", "in", logins)]).ids)

    def _bf_work_noted_by(self):
        self.ensure_one()
        if impersonating():
            return "other"
        if self.env.su and self.env.uid == SUPERUSER_ID:
            # 🟠 A job or a sync, yes; a request served as superuser (a federation
            # inbox receiving a peer's report) is someone else.
            return "other" if tools.request else "trusted"
        if self.env.uid in self.partner_id.user_ids.ids or self.env.uid in self._bf_work_processing_ids():
            return "trusted"
        return "other"

    def _bf_work_stamp(self):
        for line in self:
            super(MeetingAttendance, line.sudo()).write({
                "bf_work_status_by": line._bf_work_noted_by(),
                # Under an impersonation, not the impersonated account: naming it as
                # processing later would turn this note « trusted ».
                "bf_work_status_uid": False if impersonating() else line.env.uid,
                "bf_work_status_start": line.meeting_id.date,
                "bf_work_status_minutes": line.meeting_id.duration_minutes,
            })

    @api.model_create_multi
    def create(self, vals_list):
        clean = {k: v for k, v in self.env.context.items()
                 if not (k.startswith("default_") and k[len("default_"):] in STAMP_FIELDS)}
        lines = super(MeetingAttendance, self.with_context(clean)).create(
            [{k: v for k, v in vals.items() if k not in STAMP_FIELDS} for vals in vals_list])
        lines._bf_work_stamp()
        return lines

    def write(self, vals):
        vals = {k: v for k, v in vals.items() if k not in STAMP_FIELDS}
        res = super().write(vals)
        # Moving a line to another person or another report is a new note.
        if {"status", "partner_id", "meeting_id"} & set(vals):
            self._bf_work_stamp()
        return res

    @api.model
    def _bf_work_restamp_processing(self):
        """The processing accounts were named after the installation: their presences,
        stamped « other » then, become trusted (judged by their last writer)."""
        ids = list(self._bf_work_processing_ids())
        if not ids:
            return 0
        self.env.flush_all()
        self.env.cr.execute("""
            UPDATE meeting_attendance SET bf_work_status_by = 'trusted'
             WHERE bf_work_status_by = 'other' AND bf_work_status_uid = ANY(%s)
        """, [ids])
        self.invalidate_model(["bf_work_status_by"])
        return self.env.cr.rowcount

    @api.model
    def _bf_work_stamp_existing(self):
        """At installation: stamp the presences noted before, from their last writer."""
        self.env.flush_all()
        trusted = list(self._bf_work_processing_ids() | {SUPERUSER_ID})
        self.env.cr.execute("""
            UPDATE meeting_attendance a
               SET bf_work_status_by = CASE
                       WHEN a.write_uid = ANY(%s) THEN 'trusted'
                       WHEN EXISTS (SELECT 1 FROM res_users u
                                     WHERE u.id = a.write_uid AND u.partner_id = a.partner_id) THEN 'trusted'
                       ELSE 'other' END,
                   bf_work_status_uid = a.write_uid,
                   bf_work_status_start = r.date,
                   bf_work_status_minutes = r.duration_minutes
              FROM meeting_record r
             WHERE r.id = a.meeting_id AND a.bf_work_status_by IS NULL
        """, [trusted])
        self.invalidate_model(list(STAMP_FIELDS))
        return self.env.cr.rowcount


class MeetingRecord(models.Model):
    _inherit = "meeting.record"

    def write(self, vals):
        res = super().write(vals)
        if {"date", "duration_minutes"} & set(vals):
            # The person or the processing (refining the report) carries the
            # presence to the new hours; a colleague stretching it does not.
            for line in self.attendance_ids.filtered(lambda l: l.bf_work_status_by == "trusted"):
                if line._bf_work_noted_by() == "trusted":
                    super(MeetingAttendance, line.sudo()).write({
                        "bf_work_status_start": line.meeting_id.date,
                        "bf_work_status_minutes": line.meeting_id.duration_minutes,
                    })
        return res


class IrConfigParameter(models.Model):
    _inherit = "ir.config_parameter"

    @api.model_create_multi
    def create(self, vals_list):
        params = super().create(vals_list)
        if any(p.key == PROCESSING_PARAM for p in params):
            self.env["meeting.attendance"].sudo()._bf_work_restamp_processing()
        return params

    def write(self, vals):
        res = super().write(vals)
        if any(p.key == PROCESSING_PARAM for p in self):
            self.env["meeting.attendance"].sudo()._bf_work_restamp_processing()
        return res

