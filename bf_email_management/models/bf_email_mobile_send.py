"""Send-once ledger for the mobile API.

A phone that composed a reply while offline replays it when the network comes
back. The dangerous case is not "the send failed" — it is **the send
succeeded and the response never arrived**: the app cannot tell that apart
from a genuine failure, so it retries, and the correspondent receives the same
message twice. On client mail that is not a cosmetic bug.

So the app stamps every send with a token it generated before the first
attempt, and replays carry the *same* token. The unique index below is what
makes the second attempt a no-op — enforced by the database rather than by a
read-then-write, which would simply move the race somewhere less visible.
"""
import logging
from datetime import timedelta

import psycopg2

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

# How long a token is remembered. Comfortably longer than any plausible
# offline stretch, short enough that the table stays small.
TOKEN_TTL_DAYS = 30


class BfEmailMobileSend(models.Model):
    _name = "bf.email.mobile.send"
    _description = "Envoi mobile déjà effectué (anti-doublon)"
    _order = "id desc"

    token = fields.Char(required=True, index=True, copy=False)
    user_id = fields.Many2one("res.users", required=True, ondelete="cascade",
                              index=True)

    _sql_constraints = [
        ("token_uniq", "unique(token)", "Cet envoi a déjà été effectué."),
    ]

    @api.model
    def _claim(self, token, user_id=None):
        """Reserve ``token``. True when newly claimed, False when already sent.

        The INSERT runs inside a savepoint: a duplicate raises
        ``UniqueViolation``, and without the savepoint that error would abort
        the whole enclosing transaction — taking the caller's send with it
        instead of quietly reporting "already done".
        """
        if not token:
            return True  # no token supplied → caller opted out of dedup
        try:
            with self.env.cr.savepoint():
                self.sudo().create({
                    "token": str(token)[:64],
                    "user_id": user_id or self.env.uid,
                })
        except psycopg2.errors.UniqueViolation:
            _logger.info("bf.email mobile: envoi %s déjà effectué, rejoué en vain.",
                         str(token)[:12])
            return False
        except Exception:  # noqa: BLE001
            # Never let bookkeeping block a legitimate send.
            _logger.warning("bf.email mobile: réservation du jeton d'envoi "
                            "impossible, envoi autorisé.", exc_info=True)
            return True
        return True

    @api.model
    def _gc(self, days=TOKEN_TTL_DAYS):
        stale = self.sudo().search([
            ("create_date", "<", fields.Datetime.now() - timedelta(days=days)),
        ])
        count = len(stale)
        if stale:
            stale.unlink()
        return count
