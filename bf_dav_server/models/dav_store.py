# Part of bf_dav_server. See LICENSE file for full copyright and licensing details.
import json
import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

SYNC_RETENTION_DAYS = 90


class BfDavChange(models.Model):
    """Change journal behind sync tokens (RFC 6578) and collection tags.

    One row per (collection, resource) change. A token is the highest row id a
    client has seen; asking for changes since a token lists the resources
    touched after it. Whether each one is still in the collection is decided
    when the client asks, so a resource that left (attendee removed, moved to
    another calendar, deleted) is reported gone.
    """

    _name = "bf.dav.change"
    _description = "DAV change journal"
    _log_access = False
    _order = "id"

    collection_key = fields.Char(required=True, index=True)
    resource_key = fields.Char(index=True)
    href = fields.Char()
    date = fields.Datetime(required=True, default=fields.Datetime.now, index=True)

    @api.model
    def _record(self, collection_keys, resource_key, href):
        keys = sorted({k for k in collection_keys if k})
        if not keys:
            return
        self.env.cr.execute(
            "INSERT INTO bf_dav_change (collection_key, resource_key, href, date) "
            "SELECT k, %s, %s, now() at time zone 'utc' FROM unnest(%s::varchar[]) AS k",
            [resource_key or None, href or None, keys])

    @api.model
    def _record_many(self, rows):
        """rows: iterable of (collection_key, resource_key, href)."""
        rows = sorted({r for r in rows if r[0]})
        if not rows:
            return
        cks, rks, hrefs = zip(*rows)
        self.env.cr.execute(
            "INSERT INTO bf_dav_change (collection_key, resource_key, href, date) "
            "SELECT c, r, h, now() at time zone 'utc' "
            "FROM unnest(%s::varchar[], %s::varchar[], %s::varchar[]) AS t(c, r, h)",
            [list(cks), list(rks), list(hrefs)])

    @api.model
    def _defer_events(self, event_ids):
        """Journal these events at the end of the transaction, once.

        Odoo writes a series of 720 occurrences record by record in many of
        its flows; journaling each write on the spot cost 4 to 5 times the
        write itself.
        """
        if not event_ids:
            return
        data = self.env.cr.precommit.data
        pending = data.setdefault("bf_dav.pending_events", set())
        if not data.get("bf_dav.pending_hooked"):
            data["bf_dav.pending_hooked"] = True
            env = self.env
            self.env.cr.precommit.add(lambda: env["bf.dav.change"]._flush_pending())
        pending.update(event_ids)

    @api.model
    def _flush_pending(self):
        data = self.env.cr.precommit.data
        pending = data.get("bf_dav.pending_events")
        if not pending:
            return
        ids = list(pending)
        pending.clear()
        from .calendar_event import _journal_rows
        self.env.cr.execute("SELECT id FROM calendar_event WHERE id = ANY(%s)", [ids])
        alive = [r[0] for r in self.env.cr.fetchall()]
        self._record_many(_journal_rows(self.env, alive))

    @api.model
    def _token(self, collection_key):
        self._flush_pending()
        self.env.cr.execute(
            "SELECT max(id) FROM bf_dav_change WHERE collection_key = %s", [collection_key])
        return self.env.cr.fetchone()[0] or 0

    @api.model
    def _floor(self):
        return int(self.env["ir.config_parameter"].sudo().get_param(
            "bf_dav_server.sync_floor", "0") or 0)

    @api.model
    def _changes_since(self, collection_key, token):
        """(resource_key, href) touched after ``token`` in this collection, last href wins."""
        self._flush_pending()
        self.env.cr.execute(
            "SELECT DISTINCT ON (resource_key, href) resource_key, href FROM bf_dav_change "
            "WHERE collection_key = %s AND id > %s AND resource_key IS NOT NULL "
            "ORDER BY resource_key, href, id DESC",
            [collection_key, token])
        return self.env.cr.fetchall()

    @api.model
    def _cron_purge(self):
        """Forget changes older than the retention; older tokens become invalid
        and their clients resynchronize in full (403 valid-sync-token)."""
        self.env.cr.execute(
            "SELECT max(id) FROM bf_dav_change WHERE date < (now() at time zone 'utc') - %s * interval '1 day'",
            [SYNC_RETENTION_DAYS])
        floor = self.env.cr.fetchone()[0]
        if not floor:
            return
        # Keep the newest row of each collection so its tag stays meaningful.
        self.env.cr.execute(
            "DELETE FROM bf_dav_change c WHERE c.id <= %s AND c.id NOT IN "
            "(SELECT max(id) FROM bf_dav_change GROUP BY collection_key)", [floor])
        self.env["ir.config_parameter"].sudo().set_param("bf_dav_server.sync_floor", str(floor))
        _logger.info("bf_dav_server: change journal purged up to %s", floor)


class BfDavUserData(models.Model):
    """What one user's clients keep in an object that Odoo does not model:
    their reminders as written (VALARM with UID, ACKNOWLEDGED, snoozes) and
    Thunderbird's reminder state (X-MOZ-LASTACK, X-MOZ-SNOOZE-TIME)."""

    _name = "bf.dav.user.data"
    _description = "DAV per-user object data"

    user_id = fields.Many2one("res.users", required=True, ondelete="cascade", index=True)
    resource_key = fields.Char(required=True, index=True)
    valarms = fields.Text(default="{}", help="JSON: component key -> raw VALARM blocks")
    moz = fields.Text(default="{}", help="JSON: Thunderbird reminder properties")

    _sql_constraints = [
        ("user_resource_uniq", "unique (user_id, resource_key)", "One row per user and object."),
    ]

    @api.model
    def _get(self, user, resource_key):
        return self.sudo().search([("user_id", "=", user.id), ("resource_key", "=", resource_key)], limit=1)

    @api.model
    def _load(self, user, resource_key):
        rec = self._get(user, resource_key)
        if not rec:
            return {}, {}
        return _loads(rec.valarms), _loads(rec.moz)

    @api.model
    def _store(self, user, resource_key, valarms, moz):
        rec = self._get(user, resource_key)
        vals = {"valarms": json.dumps(valarms, sort_keys=True), "moz": json.dumps(moz, sort_keys=True)}
        if rec:
            if rec.valarms != vals["valarms"] or rec.moz != vals["moz"]:
                rec.write(vals)
        elif valarms or moz:
            self.sudo().create(dict(vals, user_id=user.id, resource_key=resource_key))


class BfDavResourceExtra(models.Model):
    """Properties of an object that Odoo does not model and that belong to the
    object, not to a user (X-MOZ-GENERATION, ATTACH, X-APPLE-*, CONFERENCE...)."""

    _name = "bf.dav.resource.extra"
    _description = "DAV object extra properties"

    resource_key = fields.Char(required=True, index=True)
    props = fields.Text(default="{}", help="JSON: component key -> raw property lines")

    _sql_constraints = [("resource_uniq", "unique (resource_key)", "One row per object.")]

    @api.model
    def _load(self, resource_key):
        rec = self.sudo().search([("resource_key", "=", resource_key)], limit=1)
        return _loads(rec.props) if rec else {}

    @api.model
    def _store(self, resource_key, props):
        rec = self.sudo().search([("resource_key", "=", resource_key)], limit=1)
        text = json.dumps(props, sort_keys=True)
        if rec:
            if rec.props != text:
                rec.write({"props": text})
        elif props:
            self.sudo().create({"resource_key": resource_key, "props": text})

    @api.model
    def _stamps(self, resource_keys):
        if not resource_keys:
            return {}
        self.env.cr.execute(
            "SELECT resource_key, write_date FROM bf_dav_resource_extra WHERE resource_key = ANY(%s)",
            [list(resource_keys)])
        return dict(self.env.cr.fetchall())


def _loads(text):
    try:
        value = json.loads(text or "{}")
        return value if isinstance(value, dict) else {}
    except ValueError:
        return {}
