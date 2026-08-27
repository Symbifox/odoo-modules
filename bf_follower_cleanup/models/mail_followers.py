import logging

from odoo import api, models

_logger = logging.getLogger(__name__)

PARAM_ALWAYS_REMOVE = "bf_follower_cleanup.always_remove_partner_ids"
PARAM_BATCH = "bf_follower_cleanup.batch_size"
PARAM_CRM_UNFOLLOW_UIDS = "bf_follower_cleanup.crm_lead_unfollow_user_ids"
PARAM_CRM_KEEP_IDS = "bf_follower_cleanup.crm_lead_keep_ids"


class MailFollowers(models.Model):
    _inherit = "mail.followers"

    @api.model
    def _bf_int_list(self, param):
        raw = self.env["ir.config_parameter"].sudo().get_param(param, "")
        ids = []
        for token in (raw or "").replace(";", ",").split(","):
            token = token.strip()
            if token.isdigit():
                ids.append(int(token))
        return ids

    @api.model
    def _bf_always_remove_partner_ids(self):
        return self._bf_int_list(PARAM_ALWAYS_REMOVE)

    @api.model
    def _bf_batch_size(self):
        batch_param = self.env["ir.config_parameter"].sudo().get_param(PARAM_BATCH, "5000")
        try:
            return max(1, int(batch_param))
        except (TypeError, ValueError):
            return 5000

    @api.model
    def _cron_remove_non_internal_followers(self):
        """Remove follower rows whose partner isn't an internal user.

        An internal user is `res.users` with `share = false` (active or archived).
        Partner IDs listed in `bf_follower_cleanup.always_remove_partner_ids`
        are purged unconditionally (e.g. service accounts like Meeting Processor API).
        """
        batch_size = self._bf_batch_size()
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

    @api.model
    def _cron_unfollow_leads_not_owned(self):
        """Unsubscribe configured users from the leads they don't sell.

        Odoo subscribes people to a lead as a side effect of ordinary work:
        `message_post` subscribes the author of any message whose
        `message_type` isn't `notification`, and `mail.activity` subscribes
        the assignee on create and on reassignment. Assigning a colleague an
        activity on their own lead is therefore enough to follow that lead for
        good, and every "Discussions" message posted on it then notifies you —
        which `bf_email_management` mirrors into your inbox as an unread item.

        Users listed in `bf_follower_cleanup.crm_lead_unfollow_user_ids` are
        unsubscribed from every lead whose salesperson isn't them, except when
        they still have an open activity assigned to them on it, or when the
        lead is listed in `bf_follower_cleanup.crm_lead_keep_ids`. A lead with
        no salesperson counts as "not theirs".

        Only ever unlinks: a lead dropped from the keep list is not re-followed.
        Empty user list (the default) is a no-op, so tenants that never opted
        in are untouched.
        """
        uids = self._bf_int_list(PARAM_CRM_UNFOLLOW_UIDS)
        if not uids or "crm.lead" not in self.env:
            return 0

        keep_ids = self._bf_int_list(PARAM_CRM_KEEP_IDS)

        # `active = TRUE` and not `IS NOT FALSE` on purpose: legacy rows carry a
        # NULL, which the ORM reads as archived, so they must not shield a lead.
        self.env.cr.execute(
            """
            SELECT DISTINCT mf.id
            FROM mail_followers mf
            JOIN res_users ru ON ru.partner_id = mf.partner_id
            JOIN crm_lead cl ON cl.id = mf.res_id
            WHERE mf.res_model = 'crm.lead'
              AND ru.id = ANY(%s)
              AND cl.user_id IS DISTINCT FROM ru.id
              AND NOT (cl.id = ANY(%s))
              AND NOT EXISTS (
                    SELECT 1
                    FROM mail_activity ma
                    WHERE ma.res_model = 'crm.lead'
                      AND ma.res_id = cl.id
                      AND ma.user_id = ru.id
                      AND ma.active = TRUE
              )
            LIMIT %s
            """,
            (uids, keep_ids or [0], self._bf_batch_size()),
        )
        ids = [row[0] for row in self.env.cr.fetchall()]
        if not ids:
            return 0

        _logger.info(
            "bf_follower_cleanup: unfollowing %d crm.lead row(s) for user(s) %s "
            "(keep_ids=%s)",
            len(ids),
            uids,
            keep_ids,
        )
        self.browse(ids).unlink()
        return len(ids)
