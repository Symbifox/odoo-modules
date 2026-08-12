"""Pre-migrate for 18.0.4.0.0 — per-user pivot.

Pre-creates ``bf_email.user_id`` / ``bf_email.account_id`` /
``bf_email_rule.user_id`` columns and backfills them to the lowest
active non-share user id (the legacy single-mailbox owner), so Odoo's
model setup doesn't fail the ``required=True`` NOT NULL on existing
rows. The owner can be overridden by setting the
``bf_email_management.legacy_owner_uid`` ir.config_parameter before
running the upgrade.

Also drops the legacy ``UNIQUE(message_id_header, company_id)``
constraint so the new ``UNIQUE(message_id_header, company_id, user_id)``
can be installed without collision.
"""

import logging

_logger = logging.getLogger(__name__)

LEGACY_OWNER_ICP_KEY = "bf_email_management.legacy_owner_uid"


def _resolve_legacy_owner_uid(cr):
    """Return the user id to assign pre-pivot bf.email rows to.

    Order of resolution:
    1. ``ir.config_parameter['bf_email_management.legacy_owner_uid']``
       if set (sysadmin override before upgrade).
    2. Lowest active non-share internal user id ``> 1`` (i.e. the first
       real human user, excluding ``__system__``).
    3. ``1`` (``SUPERUSER_ID``) as a final fallback so the migration
       never crashes on an empty tenant.
    """
    cr.execute(
        "SELECT value FROM ir_config_parameter WHERE key = %s",
        [LEGACY_OWNER_ICP_KEY],
    )
    row = cr.fetchone()
    if row and row[0]:
        try:
            return int(row[0])
        except (TypeError, ValueError):
            pass
    cr.execute(
        "SELECT MIN(id) FROM res_users "
        "WHERE share = FALSE AND active = TRUE AND id > 1"
    )
    row = cr.fetchone()
    return row[0] if row and row[0] else 1


def migrate(cr, version):
    if not version:
        return

    owner_uid = _resolve_legacy_owner_uid(cr)
    _logger.info(
        "bf_email_management 18.0.4.0.0 pre-migrate: resolved legacy "
        "owner uid = %s", owner_uid,
    )

    # bf.email — add user_id + account_id columns and backfill.
    cr.execute("""
        ALTER TABLE bf_email
            ADD COLUMN IF NOT EXISTS user_id INTEGER
    """)
    cr.execute("""
        ALTER TABLE bf_email
            ADD COLUMN IF NOT EXISTS account_id INTEGER
    """)
    cr.execute(
        "UPDATE bf_email SET user_id = %s WHERE user_id IS NULL",
        [owner_uid],
    )
    _logger.info(
        "bf_email_management 18.0.4.0.0 pre-migrate: %s rows backfilled "
        "(bf_email.user_id = %s)",
        cr.rowcount, owner_uid,
    )

    # bf.email.rule — add user_id and backfill.
    cr.execute("""
        ALTER TABLE bf_email_rule
            ADD COLUMN IF NOT EXISTS user_id INTEGER
    """)
    cr.execute(
        "UPDATE bf_email_rule SET user_id = %s WHERE user_id IS NULL",
        [owner_uid],
    )
    _logger.info(
        "bf_email_management 18.0.4.0.0 pre-migrate: %s rule rows backfilled",
        cr.rowcount,
    )

    # Drop the legacy company-scoped UNIQUE constraint so the new
    # (message_id_header, company_id, user_id) constraint can be added by
    # Odoo's _auto_init without collision. The actual constraint name was
    # generated from _sql_constraints[0][0] = "message_id_header_uniq",
    # so PostgreSQL stored it as "bf_email_message_id_header_uniq".
    cr.execute(
        "ALTER TABLE bf_email DROP CONSTRAINT IF EXISTS "
        "bf_email_message_id_header_uniq"
    )
    _logger.info(
        "bf_email_management 18.0.4.0.0 pre-migrate: legacy UNIQUE constraint dropped"
    )

    # Drop legacy ACL/rule/membership rows that point to the obsolete groups
    # BEFORE Odoo re-loads ir.model.access.csv. Without this cleanup, the
    # post-migrate ``group.unlink()`` trips a foreign key violation because
    # ir_model_access rows for the legacy CSV ids are still in place at the
    # time Odoo reads ``ir.model.data``.
    cr.execute("""
        DELETE FROM ir_model_access
              WHERE id IN (
                  SELECT res_id FROM ir_model_data
                   WHERE module = 'bf_email_management'
                     AND model  = 'ir.model.access'
              )
    """)
    cr.execute("""
        DELETE FROM ir_model_data
              WHERE module = 'bf_email_management'
                AND model  = 'ir.model.access'
    """)
    # res.groups.users (M2M) and ir.rule.groups (M2M) — find via group xml_ids.
    cr.execute("""
        SELECT res_id FROM ir_model_data
         WHERE module = 'bf_email_management'
           AND model  = 'res.groups'
           AND name IN ('group_email_user', 'group_email_manager')
    """)
    legacy_group_ids = [row[0] for row in cr.fetchall()]
    if legacy_group_ids:
        cr.execute(
            "DELETE FROM res_groups_users_rel WHERE gid IN %s",
            [tuple(legacy_group_ids)],
        )
        cr.execute(
            "DELETE FROM rule_group_rel WHERE group_id IN %s",
            [tuple(legacy_group_ids)],
        )
        # implied_ids M2M (group_id_ref ↔ group_id_dest)
        cr.execute(
            "DELETE FROM res_groups_implied_rel "
            "WHERE gid IN %s OR hid IN %s",
            [tuple(legacy_group_ids), tuple(legacy_group_ids)],
        )
        _logger.info(
            "bf_email_management 18.0.4.0.0 pre-migrate: cleaned FKs for "
            "legacy groups %s", legacy_group_ids,
        )
