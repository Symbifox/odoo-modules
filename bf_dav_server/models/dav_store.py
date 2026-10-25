# Part of bf_dav_server. See LICENSE file for full copyright and licensing details.
import collections
import datetime
import json
import logging

import pytz

from odoo import api, fields, models

from ..dav import ical

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
        self.env.cr.cache.pop("bf_dav_own_alarms", None)
        vals = {"valarms": json.dumps(valarms, sort_keys=True), "moz": json.dumps(moz, sort_keys=True)}
        if rec:
            if rec.valarms != vals["valarms"] or rec.moz != vals["moz"]:
                rec.write(vals)
        elif valarms or moz:
            self.sudo().create(dict(vals, user_id=user.id, resource_key=resource_key))

    # -- Symbifox Mobile (bf_email_management) -----------------------------
    # An attendee's own reminders, set in Thunderbird or Apple, live here and
    # not in the meeting's alarm_ids: without these two, the phone rang the
    # organizer's reminders instead.

    @api.model
    def _bf_own_alarms(self):
        """(user id, resource key) -> stored VALARMs, for the rows that hold
        some: read once per transaction (the phone's cron asks for every
        attendee of every upcoming meeting, each minute)."""
        cache = self.env.cr.cache
        if "bf_dav_own_alarms" not in cache:
            self.flush_model(["user_id", "resource_key", "valarms"])
            # CardDAV keeps a card's extra lines in the same column (keys "p<id>"):
            # meetings only.
            self.env.cr.execute("SELECT user_id, resource_key, valarms FROM bf_dav_user_data "
                                "WHERE (resource_key LIKE 'e%%' OR resource_key LIKE 'r%%') "
                                "AND valarms IS NOT NULL AND valarms NOT IN ('', '{}')")
            cache["bf_dav_own_alarms"] = {(uid, key): stored for uid, key, text in self.env.cr.fetchall()
                                          if any((stored := _loads(text)).values())}
        return cache["bf_dav_own_alarms"]

    @api.model
    def _bf_reminder_events(self, now):
        """Upcoming events where someone keeps reminders of their own. Starts
        a pass of the phone's cron: the cursor's cache outlives a commit."""
        self.env.cr.cache.pop("bf_dav_own_alarms", None)
        ev_ids, rec_ids = set(), set()
        for _uid, key in self._bf_own_alarms():
            if key[:1] == "e" and key[1:].isdigit():
                ev_ids.add(int(key[1:]))
            elif key[:1] == "r" and key[1:].isdigit():
                rec_ids.add(int(key[1:]))
        Event = self.env["calendar.event"].sudo()
        if not ev_ids and not rec_ids:
            return Event
        return Event.search([("active", "=", True), ("start", ">", now),
                             "|", ("id", "in", list(ev_ids)), ("recurrence_id", "in", list(rec_ids))])

    @api.model
    def _bf_reminder_triggers(self, attendee, event):
        """(time, reminder) pairs from the attendee's own reminders, the way
        the server serves them to that person (``_valarms_out``); None when the
        meeting's reminders apply: the organizer or the calendar's owner, or
        nobody kept anything for this occurrence."""
        from .dav_caldav import _rid_key
        user = attendee.partner_id.user_ids.filtered(lambda u: u.active and not u.share)[:1]
        ev = event.sudo()
        if not user or not ev.start or ev.user_id == user or ev.bf_dav_calendar_id.owner_id == user:
            return None
        key = ("r%d" % ev.recurrence_id.id) if ev.recurrence_id else ("e%d" % ev.id)
        stored = self._bf_own_alarms().get((user.id, key))
        if not stored:
            return None
        ck = ""
        if ev.recurrence_id:
            ck = _rid_key(ev.bf_dav_recurrence_id or ev.start, ev.allday)
            if not stored.get(ck):
                ck = ""
        parsed = [ical.parse_alarm(raw) for raw in stored.get(ck, [])]
        if not parsed:
            return None
        base = ev.start
        if ev.allday and ev.start_date:
            # Odoo puts an all-day meeting at 08:00; apps count from midnight,
            # in the person's zone.
            tz = pytz.timezone(user.tz or ev.event_tz or "UTC")
            base = tz.localize(datetime.datetime.combine(ev.start_date, datetime.time())
                               ).astimezone(pytz.utc).replace(tzinfo=None)
        out = []
        for alarm in parsed:
            # A snooze is bf_snoozed_until's business; Apple's default reminder
            # belongs to the device that added it.
            if alarm.snooze_of or alarm.default_alarm or alarm.action not in ("DISPLAY", "AUDIO"):
                continue
            if alarm.minutes_before is not None:
                when = base - datetime.timedelta(minutes=alarm.minutes_before)
            elif alarm.absolute and not ev.recurrence_id:
                # A fixed time on a series would ring every occurrence at once.
                when = alarm.absolute
            else:
                continue
            minutes = max(int((base - when).total_seconds() // 60), 0)
            out.append((when, _Reminder(minutes)))
        # Nothing the phone can ring (only END-related, Apple's default...):
        # the meeting's reminders rather than silence.
        return out or None


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


_Reminder = collections.namedtuple("_Reminder", "duration_minutes")


def _loads(text):
    try:
        value = json.loads(text or "{}")
        return value if isinstance(value, dict) else {}
    except ValueError:
        return {}
