import logging

from odoo import api, models

_logger = logging.getLogger(__name__)

PARAM_ALWAYS_REMOVE = "bf_follower_cleanup.always_remove_partner_ids"
PARAM_BATCH = "bf_follower_cleanup.batch_size"


class MailFollowers(models.Model):
    _inherit = "mail.followers"

    @api.model
    def _bf_always_remove_partner_ids(self):
        raw = self.env["ir.config_parameter"].sudo().get_param(PARAM_ALWAYS_REMOVE, "")
        ids = []
        for token in (raw or "").replace(";", ",").split(","):
            token = token.strip()
            if token.isdigit():
                ids.append(int(token))
        return ids

    @api.model
    def _cron_remove_non_internal_followers(self):
        """Remove follower rows whose partner isn't an internal user.

        An internal user is `res.users` with `share = false` (active or archived).
        Partner IDs listed in `bf_follower_cleanup.always_remove_partner_ids`
        are purged unconditionally (e.g. integration/service accounts).
        """
        batch_param = self.env["ir.config_parameter"].sudo().get_param(PARAM_BATCH, "5000")
        try:
            batch_size = max(1, int(batch_param))
        except (TypeError, ValueError):
            batch_size = 5000

        always_remove = self._bf_always_remove_partner_ids()

        self.env.cr.execute(
            """
            SELECT mf.id
            FROM mail_followers mf
            WHERE mf.partner_id = ANY(%s)
               OR NOT EXISTS (
                    SELECT 1
                    FROM res_users ru
                    WHERE ru.partner_id = mf.partner_id
                      AND ru.share = FALSE
               )
            LIMIT %s
            """,
            (always_remove or [0], batch_size),
        )
        ids = [row[0] for row in self.env.cr.fetchall()]
        if not ids:
            return 0

        _logger.info(
            "bf_follower_cleanup: removing %d non-internal follower row(s) "
            "(always_remove_partner_ids=%s)",
            len(ids),
            always_remove,
        )
        self.browse(ids).unlink()
        return len(ids)
