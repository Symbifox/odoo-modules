# Part of bf_dav_server. See LICENSE file for full copyright and licensing details.
"""Installation: give every existing event, series and contact a stable
identity, and create the collections clients will see.

On a database that syncs with Nextcloud, each Nextcloud calendar becomes a
CalDAV calendar of its owner (same name), with the same UIDs, so that moving a
client from Nextcloud to Odoo shows the same events, not copies.
"""

import logging

from .models.dav_calendar import slugify

_logger = logging.getLogger(__name__)

SAFE = "^[A-Za-z0-9._@-]{1,200}$"


def _columns(cr, table):
    cr.execute("SELECT column_name FROM information_schema.columns WHERE table_name = %s", [table])
    return {r[0] for r in cr.fetchall()}


def _coalesce(cols, *names):
    present = ["NULLIF(trim(%s), '')" % n for n in names if n in cols]
    return "COALESCE(%s)" % ", ".join(present + ["gen_random_uuid()::text"])


def backfill_identities(env):
    cr = env.cr
    ev_cols = _columns(cr, "calendar_event")
    # Series first: the UID Nextcloud knew for the series.
    src = [n for n in ("x_nc_uid", "bf_ics_uid") if n in ev_cols]
    if src:
        expr = "COALESCE(%s)" % ", ".join("NULLIF(trim(e.%s), '')" % n for n in src)
        cr.execute("""
            UPDATE calendar_recurrence r SET bf_dav_uid = s.uid FROM (
                SELECT DISTINCT ON (e.recurrence_id) e.recurrence_id AS rid, {expr} AS uid
                  FROM calendar_event e
                 WHERE e.recurrence_id IS NOT NULL AND {expr} IS NOT NULL
                 ORDER BY e.recurrence_id, e.id) s
             WHERE r.id = s.rid AND r.bf_dav_uid IS NULL
        """.format(expr=expr))
    cr.execute("UPDATE calendar_recurrence SET bf_dav_uid = gen_random_uuid()::text WHERE bf_dav_uid IS NULL")
    # Single events keep their Nextcloud UID; occurrences get their own (unused while in a series).
    cr.execute("UPDATE calendar_event SET bf_dav_uid = %s WHERE bf_dav_uid IS NULL AND recurrence_id IS NULL"
               % _coalesce(ev_cols, *src))
    cr.execute("UPDATE calendar_event SET bf_dav_uid = gen_random_uuid()::text WHERE bf_dav_uid IS NULL")
    # Two singles with one UID, or a single carrying its old series' UID: renew the later ones.
    cr.execute("""
        UPDATE calendar_event e SET bf_dav_uid = gen_random_uuid()::text
         WHERE e.recurrence_id IS NULL AND (
               EXISTS (SELECT 1 FROM calendar_event o WHERE o.recurrence_id IS NULL
                        AND o.bf_dav_uid = e.bf_dav_uid AND o.id < e.id)
            OR EXISTS (SELECT 1 FROM calendar_recurrence r WHERE r.bf_dav_uid = e.bf_dav_uid))
    """)
    cr.execute("""
        UPDATE calendar_recurrence r SET bf_dav_uid = gen_random_uuid()::text
         WHERE EXISTS (SELECT 1 FROM calendar_recurrence o WHERE o.bf_dav_uid = r.bf_dav_uid AND o.id < r.id)
    """)
    for table in ("calendar_event", "calendar_recurrence"):
        cr.execute("""
            UPDATE %s SET bf_dav_href = CASE WHEN bf_dav_uid ~ '%s' THEN bf_dav_uid || '.ics'
                                             ELSE md5(bf_dav_uid) || '.ics' END
             WHERE bf_dav_href IS NULL
        """ % (table, SAFE))
    rid_src = [n for n in ("x_nc_recurrence_id", "bf_ics_recurrence_id") if n in ev_cols]
    cr.execute("UPDATE calendar_event SET bf_dav_recurrence_id = COALESCE(%s) "
               "WHERE recurrence_id IS NOT NULL AND bf_dav_recurrence_id IS NULL"
               % ", ".join(rid_src + ["start"]))
    # Contacts
    p_cols = _columns(cr, "res_partner")
    cr.execute("UPDATE res_partner SET bf_dav_uid = %s WHERE bf_dav_uid IS NULL"
               % _coalesce(p_cols, "x_nc_contact_uid"))
    cr.execute("""
        UPDATE res_partner p SET bf_dav_uid = gen_random_uuid()::text
         WHERE EXISTS (SELECT 1 FROM res_partner o WHERE o.bf_dav_uid = p.bf_dav_uid AND o.id < p.id)
    """)
    cr.execute("""
        UPDATE res_partner SET bf_dav_href = CASE WHEN bf_dav_uid ~ '%s' THEN bf_dav_uid || '.vcf'
                                                  ELSE md5(bf_dav_uid) || '.vcf' END
         WHERE bf_dav_href IS NULL
    """ % SAFE)
    env.invalidate_all()


def create_collections(env):
    cr = env.cr
    Cal = env["bf.dav.calendar"].sudo()
    has_default = set()
    mapped = []
    # Played again (an install cut short and resumed): what exists stays, nothing is doubled.
    if "nextcloud.calendar.sync.config" in env and not Cal.with_context(active_test=False).search_count([]):
        configs = env["nextcloud.calendar.sync.config"].sudo().with_context(active_test=False).search([], order="id")
        for config in configs:
            owner = config.calendar_owner_id or config.create_uid
            if not owner or owner.share or owner._is_superuser():
                continue
            cal = Cal.create({
                "name": config.name or "Calendar",
                "owner_id": owner.id,
                "slug": Cal._unique_slug(owner.id, slugify(config.name)),
                "is_default": owner.id not in has_default,
                "color": getattr(config, "calendar_color", False) or getattr(config, "color_hex", False) or "#29ABE2",
            })
            has_default.add(owner.id)
            mapped.append((config.id, cal.id))
    for config_id, cal_id in mapped:
        cr.execute("UPDATE calendar_event SET bf_dav_calendar_id = %s "
                   "WHERE x_nc_calendar_id = %s AND bf_dav_calendar_id IS NULL", [cal_id, config_id])
    set_odoo_colors(env)
    users = env["res.users"].sudo().search([("share", "=", False), ("active", "=", True)])
    for user in users:
        if user._is_superuser() or user.id in has_default:
            continue
        Cal._bf_dav_default_for(user)
    env["bf.dav.calendar"].flush_model()
    cr.execute("""
        UPDATE calendar_event e SET bf_dav_calendar_id = c.id
          FROM bf_dav_calendar c
         WHERE e.bf_dav_calendar_id IS NULL AND c.owner_id = e.user_id AND c.is_default
    """)
    env.invalidate_all()
    # The company book.
    Book = env["bf.dav.addressbook"].sudo()
    if not Book.search_count([("kind", "=", "company")]):
        tags = env["res.partner.category"]
        if "nextcloud.contacts.sync.config" in env:
            cfgs = env["nextcloud.contacts.sync.config"].sudo().with_context(active_test=False).search([])
            tags = cfgs.mapped("exclude_tag_id") if "exclude_tag_id" in cfgs._fields else tags
        Book.create({"name": env.company.name or "Company", "kind": "company", "slug": "company",
                     "exclude_tag_ids": [(6, 0, tags.ids)]})
    _logger.info("bf_dav_server: %d calendars, %d mapped from Nextcloud", Cal.search_count([]), len(mapped))


def set_odoo_colors(env):
    """Each calendar keeps the Odoo color its Nextcloud calendar had (the
    color the sync painted on its events), so the Calendar app looks the
    same once Nextcloud is gone."""
    cr = env.cr
    # By table, not by model: during a migration the sync module may not be
    # loaded yet (bf_dav_server does not depend on it).
    cols = _columns(cr, "nextcloud_calendar_sync_config")
    if "odoo_color" not in cols:
        return
    env["bf.dav.calendar"].flush_model()
    cr.execute("""
        SELECT DISTINCT ON (e.bf_dav_calendar_id) e.bf_dav_calendar_id, c.odoo_color
          FROM calendar_event e JOIN nextcloud_calendar_sync_config c ON c.id = e.x_nc_calendar_id
         WHERE e.bf_dav_calendar_id IS NOT NULL AND c.odoo_color IS NOT NULL
         GROUP BY e.bf_dav_calendar_id, c.odoo_color
         ORDER BY e.bf_dav_calendar_id, count(*) DESC
    """)
    for cal_id, color in cr.fetchall():
        cr.execute("UPDATE bf_dav_calendar SET odoo_color = %s WHERE id = %s AND COALESCE(odoo_color, 0) = 0",
                   [color, cal_id])
    env.invalidate_all()


def post_init_hook(env):
    backfill_identities(env)
    create_collections(env)
