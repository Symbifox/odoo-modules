from odoo import _, fields, models


class CalendarEvent(models.Model):
    _inherit = "calendar.event"

    def action_create_linked_activity(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Planifier une activité"),
            "res_model": "mail.activity",
            "views": [[False, "form"]],
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_calendar_event_id": self.id,
                "default_summary": self.name,
                "default_date_deadline": str(self.start.date()) if self.start else False,
            },
        }
