import logging

from odoo import models
from odoo.tools.mail import decode_message_header

_logger = logging.getLogger(__name__)


class MailThread(models.AbstractModel):
    _inherit = "mail.thread"

    def _routing_create_bounce_email(
        self, email_from, body_html, message, **mail_values
    ):
        """Never answer an unroutable incoming email with a MAILER-DAEMON reply.

        Core calls this from four places, all of which end up telling the person
        who wrote to us that their message "could not be accepted by the
        address ...". Every address Blue Fox publishes is live and monitored, so
        that statement is always false and always goes to the wrong audience:
        clients, suppliers and support desks whose only mistake was replying.

        We drop the reply and log why. The caller has already flagged the alias
        as ``invalid`` (visible under Settings > Technical > Aliases) and core
        logs the routing failure separately, so nothing is silently lost on our
        side - only the outbound reply is.
        """
        _logger.warning(
            "bf_no_gateway_bounce: suppressed gateway bounce to %s "
            "(Message-Id %s, subject %r). Alias/routing failure is still "
            "recorded on the Odoo side; the sender was not notified.",
            decode_message_header(message, "Return-Path") or email_from,
            decode_message_header(message, "Message-Id")
            or decode_message_header(message, "Message-ID"),
            decode_message_header(message, "Subject"),
        )
        return
