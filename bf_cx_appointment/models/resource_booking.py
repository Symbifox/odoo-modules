"""Post-appointment feedback request.

Hooks the bf_appointment email cron (_cron_send_appointment_emails, whose
'after' branch is the existing post-appointment rail): super() runs the
host schedules untouched, then a dedicated pass sends a 3-emoji rating
request for bookings that just finished. Opt-in via
bf_cx.appointment_feedback (default off), one request per booking
(bf_cx_feedback_requested), and the central bf_cx solicitation guard
still applies inside rating_send_request().
"""
import logging
from datetime import timedelta

from odoo import api, fields, models

from odoo.addons.bf_cx.models.bf_cx_feedback import param_is_true

_logger = logging.getLogger(__name__)

# Only bookings finished within this window are considered, so turning the
# option on never blasts feedback requests over the whole booking history.
FEEDBACK_LOOKBACK_DAYS = 7


class ResourceBooking(models.Model):
    _inherit = "resource.booking"

    bf_cx_feedback_requested = fields.Boolean(
        string="Feedback post-rendez-vous demandé", copy=False
    )

    # Postgres advisory-lock key for the feedback pass. It is DISTINCT from
    # the host cron's key (_CRON_ADVISORY_LOCK_KEY in bf_appointment): when
    # a worker loses the host lock, the host body returns early but super()
    # still returns, so without a dedicated lock this pass would run on
    # every worker in parallel and double-send.
    _BF_CX_FEEDBACK_LOCK_KEY = 0x4246435841504642  # "BFCXAPFB"

    @api.model
    def _cron_send_appointment_emails(self):
        res = super()._cron_send_appointment_emails()
        try:
            self._bf_cx_request_post_booking_feedback()
        except Exception:  # noqa: BLE001 - never break the host cron
            _logger.exception(
                "bf_cx_appointment: post-booking feedback pass failed"
            )
        return res

    def _bf_cx_request_post_booking_feedback(self):
        """Send a 3-emoji rating request for recently finished bookings.

        Serialized by its own transaction-scoped advisory lock (see
        _BF_CX_FEEDBACK_LOCK_KEY): the host cron's lock does not protect
        this pass, because the worker that loses it still reaches here.

        The bf_cx_feedback_requested flag is only set AFTER a successful
        send: a booking blocked by the anti-solicitation cooldown (or hit
        by a transient send failure) keeps its slot and is retried on a
        later tick, once the cooldown has expired.
        """
        self.env.cr.execute(
            "SELECT pg_try_advisory_xact_lock(%s)",
            [self._BF_CX_FEEDBACK_LOCK_KEY],
        )
        if not self.env.cr.fetchone()[0]:
            _logger.info(
                "bf_cx_appointment: feedback pass already running on "
                "another worker, skipping"
            )
            return
        if not param_is_true(
            self.env, "bf_cx.appointment_feedback", default=False
        ):
            return
        template = self.env.ref(
            "bf_cx_appointment.mail_template_booking_rating",
            raise_if_not_found=False,
        )
        if not template:
            return
        now = fields.Datetime.now()
        bookings = self.search([
            ("state", "in", ("confirmed", "scheduled")),
            ("stop", "!=", False),
            ("stop", "<=", now),
            ("stop", ">=", now - timedelta(days=FEEDBACK_LOOKBACK_DAYS)),
            ("bf_cx_feedback_requested", "=", False),
        ])
        for booking in bookings:
            partner = booking.partner_id
            if not partner or not partner.email:
                continue
            allowed, blocked = partner._bf_cx_split_solicitable()
            if blocked:
                # Cooldown: leave the flag unset so a later tick retries
                # once the cooldown expires. Logged (not chattered) since
                # the retry would repost the message on every tick.
                _logger.info(
                    "bf_cx_appointment: feedback for booking %s deferred, "
                    "%s was solicited recently",
                    booking.id,
                    partner.display_name,
                )
                continue
            try:
                booking.rating_send_request(
                    template, lang=partner.lang, force_send=False
                )
            except Exception:  # noqa: BLE001 - one booking, not the pass
                _logger.exception(
                    "bf_cx_appointment: rating request failed for "
                    "booking %s, will retry on a later tick",
                    booking.id,
                )
                continue
            booking.bf_cx_feedback_requested = True
            partner._bf_cx_mark_solicited()
