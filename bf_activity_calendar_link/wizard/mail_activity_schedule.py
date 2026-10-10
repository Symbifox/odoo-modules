from odoo import api, fields, models


class MailActivitySchedule(models.TransientModel):
    _inherit = "mail.activity.schedule"

    calendar_event_id = fields.Many2one(
        "calendar.event",
        string="Calendar event",
        help="Link this activity to an existing calendar event",
    )
    calendar_event_start = fields.Datetime(
        related="calendar_event_id.start",
        string="Start",
    )
    calendar_event_stop = fields.Datetime(
        related="calendar_event_id.stop",
        string="End",
    )
    calendar_event_location = fields.Char(
        related="calendar_event_id.location",
        string="Location",
    )
    calendar_event_sync_source = fields.Selection(
        related="calendar_event_id.x_sync_source",
        string="Sync source",
    )

    @api.onchange("calendar_event_id")
    def _onchange_calendar_event_id(self):
        if self.calendar_event_id:
            if self.calendar_event_id.start and (
                not self.date_deadline
                or self.date_deadline == fields.Date.context_today(self)
            ):
                self.date_deadline = self.calendar_event_id.start.date()
            if not self.summary:
                self.summary = self.calendar_event_id.name

    def _action_schedule_activities(self):
        activities = super()._action_schedule_activities()
        if self.calendar_event_id and activities:
            activities.write({"calendar_event_id": self.calendar_event_id.id})
        return activities
