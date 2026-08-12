"""Post-migrate for 18.0.4.0.0 — per-user pivot.

After Odoo has loaded the new ``bf.email.account`` model and added the
``user_id`` / ``account_id`` columns to ``bf.email``, this script:

1. Creates one ``bf.email.account`` row from the legacy ICP IMAP credentials,
   owned by the resolved legacy owner uid (see pre-migrate.py).
2. Backfills ``bf.email.account_id`` on pre-existing IMAP rows.
3. Wipes the legacy ``ir.config_parameter`` rows so no stale credentials
   linger.
4. Unlinks the legacy ``group_email_user`` / ``group_email_manager``
   groups + the module category record.
"""

import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

LEGACY_OWNER_ICP_KEY = "bf_email_management.legacy_owner_uid"

LEGACY_ICP_KEYS = (
    "bf_email.imap_host",
    "bf_email.imap_port",
    "bf_email.imap_user",
    "bf_email.imap_password",
    "bf_email.imap_archive_folder",
    "bf_email.imap_writeback_archive",
    "bf_email.imap_batch_size",
    "bf_email.imap_last_uid_inbox",
    "bf_email.imap_last_uid_sent",
    "bf_email.sync_batch_size",
    "bf_email.auto_link_threshold_days",
)


def migrate(cr, version):
    if not version:
        return

    env = api.Environment(cr, SUPERUSER_ID, {})
    ICP = env["ir.config_parameter"].sudo()
    Account = env["bf.email.account"].sudo()

    # 1. Resolve legacy owner (override via ICP or first non-share user).
    legacy_owner_param = ICP.get_param(LEGACY_OWNER_ICP_KEY)
    if legacy_owner_param:
        try:
            owner_uid = int(legacy_owner_param)
        except (TypeError, ValueError):
            owner_uid = None
    else:
        owner_uid = None
    if not owner_uid:
        cr.execute(
            "SELECT MIN(id) FROM res_users "
            "WHERE share = FALSE AND active = TRUE AND id > 1"
        )
        row = cr.fetchone()
        owner_uid = row[0] if row and row[0] else SUPERUSER_ID

    # 2. Create the legacy account from ICP, owned by the resolved owner.
    host = ICP.get_param("bf_email.imap_host")
    login = ICP.get_param("bf_email.imap_user")
    password = ICP.get_param("bf_email.imap_password")
    if host and login and password:
        existing = Account.search([
            ("user_id", "=", owner_uid),
            ("login", "=", login),
        ], limit=1)
        if not existing:
            try:
                writeback_param = (ICP.get_param(
                    "bf_email.imap_writeback_archive", "True",
                ) or "True").strip().lower()
                account = Account.create({
                    "name": login,
                    "user_id": owner_uid,
                    "host": host,
                    "port": int(ICP.get_param("bf_email.imap_port") or 993),
                    "login": login,
                    "password": password,
                    "archive_folder": ICP.get_param(
                        "bf_email.imap_archive_folder",
                    ) or "Archives/{YYYY}",
                    "batch_size": int(
                        ICP.get_param("bf_email.imap_batch_size") or 100
                    ),
                    "writeback_archive": writeback_param == "true",
                    "auto_link_threshold_days": int(
                        ICP.get_param("bf_email.auto_link_threshold_days") or 14
                    ),
                    "last_uid_inbox": int(
                        ICP.get_param("bf_email.imap_last_uid_inbox") or 0
                    ),
                    "last_uid_sent": int(
                        ICP.get_param("bf_email.imap_last_uid_sent") or 0
                    ),
                    "last_sync_date": ICP.get_param("bf_email.last_sync_date") or False,
                    "state": "draft",
                })
                _logger.info(
                    "bf_email_management 18.0.4.0.0: created account #%s "
                    "for %s (uid=%s)",
                    account.id, login, owner_uid,
                )
                # 3. Link existing IMAP bf.email rows of this owner to the account.
                cr.execute(
                    "UPDATE bf_email SET account_id = %s "
                    "WHERE account_id IS NULL AND user_id = %s "
                    "AND source = 'imap'",
                    [account.id, owner_uid],
                )
                _logger.info(
                    "bf_email_management 18.0.4.0.0: linked %s IMAP rows to account #%s",
                    cr.rowcount, account.id,
                )
            except Exception:
                _logger.exception(
                    "bf_email_management 18.0.4.0.0: account creation failed"
                )
    else:
        _logger.info(
            "bf_email_management 18.0.4.0.0: no ICP IMAP credentials, "
            "skipping account creation",
        )

    # 3. Drop legacy ICP rows.
    cr.execute(
        "DELETE FROM ir_config_parameter WHERE key IN %s",
        [LEGACY_ICP_KEYS],
    )
    _logger.info(
        "bf_email_management 18.0.4.0.0: deleted %s legacy ICP rows",
        cr.rowcount,
    )

    # 4. Drop legacy groups + category, if still present.
    for xid in (
        "bf_email_management.group_email_user",
        "bf_email_management.group_email_manager",
        "bf_email_management.module_category_email",
    ):
        rec = env.ref(xid, raise_if_not_found=False)
        if rec:
            try:
                rec.sudo().unlink()
                _logger.info(
                    "bf_email_management 18.0.4.0.0: unlinked legacy %s", xid,
                )
            except Exception:
                _logger.warning(
                    "bf_email_management 18.0.4.0.0: failed to unlink %s",
                    xid, exc_info=True,
                )
