"""Do not start chasing signers on requests that were already in flight.

v18.0.3.16.0 adds automatic reminders, and ``reminder_enabled`` defaults to
True — so on upgrade every request already sent would become eligible. Someone
who sent a document three weeks ago did not ask for it to start emailing their
counterparty today, and a client's signer receiving an unexpected chase because
we deployed is the kind of surprise that costs trust.

The feature therefore applies to requests created FROM NOW ON. Anything already
sent keeps behaving exactly as it did; the preparer can turn reminders on for a
specific one deliberately, from the form.

``invited_on`` is backfilled from the append-only journal all the same, because
the information is true and shows in the Recipients list. It stays inert while
reminders are off — and it is what would have gated them anyway, since a signer
without an ``invited_on`` is never due.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return

    cr.execute("""
        UPDATE bf_sign_request
           SET reminder_enabled = FALSE
         WHERE state IN ('sent', 'in_progress')
    """)
    frozen = cr.rowcount

    # First 'sent' journal entry naming this signer = when they were invited.
    cr.execute("""
        WITH invites AS (
            SELECT s.id AS signer_id, MIN(l.timestamp_utc) AS first_sent
              FROM bf_sign_log l
              JOIN bf_sign_signer s ON s.request_id = l.request_id
             WHERE l.event = 'sent'
               AND l.note IS NOT NULL
               AND position(lower(s.email) in lower(l.note)) > 0
             GROUP BY s.id
        )
        UPDATE bf_sign_signer s
           SET invited_on = i.first_sent::timestamptz AT TIME ZONE 'UTC'
          FROM invites i
         WHERE i.signer_id = s.id
           AND s.invited_on IS NULL
    """)
    dated = cr.rowcount

    _logger.info(
        "bf_sign: relances laissées désactivées sur %s demande(s) déjà en vol ; "
        "date d'invitation rapatriée pour %s signataire(s).", frozen, dated)
