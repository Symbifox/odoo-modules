from odoo import fields, models


class ResourceBookingType(models.Model):
    _inherit = "resource.booking.type"

    video_provider = fields.Selection(
        selection_add=[("google_meet", "Google Meet")],
        # Uninstalling the bridge must not leave types on a value nobody
        # handles: they go back to the field's default (Nextcloud Talk).
        ondelete={"google_meet": "set default"},
    )
