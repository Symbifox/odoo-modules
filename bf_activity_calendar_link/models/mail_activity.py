from odoo import api, fields, models


class MailActivity(models.Model):
    _inherit = "mail.activity"

    calendar_event_start = fields.Datetime(
        related="calendar_event_id.start",
        string="Début événement",
    )
    calendar_event_location = fields.Char(
        related="calendar_event_id.location",
        string="Lieu",
    )
    calendar_event_sync_source = fields.Selection(
        related="calendar_event_id.x_sync_source",
        string="Source sync",
    )
    calendar_event_attendee_names = fields.Char(
        string="Participants",
        compute="_compute_calendar_event_attendee_names",
    )

    @api.depends("calendar_event_id.partner_ids")
    def _compute_calendar_event_attendee_names(self):
        for activity in self:
            if activity.calendar_event_id and activity.calendar_event_id.partner_ids:
                activity.calendar_event_attendee_names = ", ".join(
                    activity.calendar_event_id.partner_ids.mapped("name")
                )
            else:
                activity.calendar_event_attendee_names = False
