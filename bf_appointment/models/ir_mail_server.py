import logging

from odoo import models

_logger = logging.getLogger(__name__)


class IrMailServer(models.Model):
    _inherit = "ir.mail_server"

    def build_email(self, *args, **kwargs):
        """Tag text/calendar parts with `method=REQUEST`.

        Stock Odoo's ICS attachment lands as `Content-Type: text/calendar`
        with no method param. Gmail/Outlook need `method=REQUEST` to treat
        the part as a real invitation (Yes/No/Maybe + auto-add to calendar)
        rather than a plain attachment. The VCALENDAR body itself is
        rewritten in calendar.event._get_ics_file; this post-processes the
        MIME header to match.
        """
        msg = super().build_email(*args, **kwargs)
        try:
            for part in msg.walk():
                if part.get_content_type() != "text/calendar":
                    continue
                payload = part.get_payload(decode=True)
                if payload and b"METHOD:REQUEST" in payload:
                    part.set_param("method", "REQUEST")
        except Exception as e:
            _logger.warning("Failed to set method=REQUEST on calendar part: %s", e)
        return msg
